"""Evaluate ledger integrity rules LED-F1 through LED-F5 over in-memory rows.

Inputs are plain row mappings from users, credit_ledger and jb2_claims; this module performs no I/O and never repairs data.
Output is a PASS or BLOCKED verdict that carries rule identifiers, counts and row identifiers, never balance amounts.
"""

from collections import Counter
from dataclasses import dataclass

# [rule identifiers]: Spec section 3.4 rule names used in findings and verdict counts.
RULE_BALANCE_MATCHES_LEDGER = "LED-F1"
RULE_AWARD_HAS_ONE_CLAIM = "LED-F2"
RULE_CLAIM_USER_MATCHES_LEDGER = "LED-F3"
RULE_NO_ORPHAN_ROWS = "LED-F4"
RULE_BALANCE_NOT_NEGATIVE = "LED-F5"
EVALUATED_RULE_IDS = (
    RULE_BALANCE_MATCHES_LEDGER,
    RULE_AWARD_HAS_ONE_CLAIM,
    RULE_CLAIM_USER_MATCHES_LEDGER,
    RULE_NO_ORPHAN_ROWS,
    RULE_BALANCE_NOT_NEGATIVE,
)
REASON_SUBMISSION_AWARD = "submission_award"
# [required claims]: Each submission_award ledger row must be backed by exactly this many claims.
REQUIRED_CLAIMS_PER_AWARD = 1
# [balance floor]: Lowest balance the schema CHECK constraint allows for users.credit_balance.
MINIMUM_CREDIT_BALANCE = 0
# [opening balance]: Balance explained by an absent ledger; no opening balance is ever assumed.
UNEXPLAINED_BASELINE_BALANCE = 0
STATUS_PASS = "PASS"
STATUS_BLOCKED = "BLOCKED"
STATUS_INCONCLUSIVE_EMPTY_SNAPSHOT = "INCONCLUSIVE_EMPTY_SNAPSHOT"
SUBJECT_USER = "user"
SUBJECT_LEDGER = "ledger"
SUBJECT_CLAIM = "claim"
# [required columns]: Columns each snapshot table must supply before any rule runs.
USER_REQUIRED_COLUMNS = ("discord_user_id", "credit_balance")
LEDGER_REQUIRED_COLUMNS = ("ledger_id", "discord_user_id", "credit_delta", "reason")
CLAIM_REQUIRED_COLUMNS = ("version_id", "ledger_id", "discord_user_id")
# [mask visibility]: Leading and trailing digits left visible in a masked Discord user identifier (LED-A3).
MASK_VISIBLE_PREFIX_LENGTH = 2
MASK_VISIBLE_SUFFIX_LENGTH = 2
MASK_CHARACTER = "*"

# Read-only reconciliation statements from docs/recovery-batchb-private-beta-spec.md section 3.6. Never executed here.
SQL_BALANCE_VS_LEDGER = """
SELECT u.discord_user_id
FROM users AS u
LEFT JOIN credit_ledger AS l ON l.discord_user_id = u.discord_user_id
GROUP BY u.discord_user_id, u.credit_balance
HAVING u.credit_balance <> COALESCE(SUM(l.credit_delta), 0);
"""
SQL_AWARD_WITHOUT_CLAIM = """
SELECT l.ledger_id, l.discord_user_id
FROM credit_ledger AS l
LEFT JOIN jb2_claims AS c ON c.ledger_id = l.ledger_id
WHERE l.reason = 'submission_award' AND c.ledger_id IS NULL;
"""
SQL_CLAIM_USER_MISMATCH = """
SELECT c.ledger_id, c.discord_user_id, l.discord_user_id AS ledger_user_id
FROM jb2_claims AS c
JOIN credit_ledger AS l ON l.ledger_id = c.ledger_id
WHERE c.discord_user_id <> l.discord_user_id;
"""
SQL_ORPHAN_LEDGER = """
SELECT l.ledger_id, l.discord_user_id
FROM credit_ledger AS l
LEFT JOIN users AS u ON u.discord_user_id = l.discord_user_id
WHERE u.discord_user_id IS NULL;
"""
SQL_NEGATIVE_BALANCE = """
SELECT discord_user_id
FROM users
WHERE credit_balance < 0;
"""


class IncompleteLedgerRowError(ValueError):
    """Raised when a snapshot row lacks a column the rules need; a silent PASS would hide missing evidence."""


class DuplicateLedgerRowError(ValueError):
    """Raised when a table snapshot repeats a primary key, which would corrupt the reconciliation sums."""


@dataclass(frozen=True)
class Finding:
    """Immutable record of one rule violation, carrying identifiers only.

    Collaborates with LedgerIntegrityVerdict; never stores balance amounts or credit deltas.
    """

    # [rule]: Rule identifier such as LED-F1 that produced this finding.
    rule_id: str
    # [subject kind]: Kind of row the identifier refers to: user, ledger or claim.
    subject_kind: str
    # [subject identifier]: Row identifier as text (no balance amounts), used for audit follow-up.
    subject_id: str


@dataclass(frozen=True)
class LedgerIntegrityVerdict:
    """Immutable outcome of one integrity evaluation, shown to the Owner as evidence.

    PASS only when findings is empty; BLOCKED otherwise. Carries no balance values.
    """

    # [verdict status]: PASS when zero findings, otherwise BLOCKED.
    status: str
    # [evaluated rules]: Every rule the evaluation ran, so PASS proves all five were checked.
    evaluated_rule_ids: tuple
    # [findings]: Ordered violations discovered across all rules.
    findings: tuple
    # [finding counts]: Number of findings per evaluated rule, including zeros.
    finding_counts: dict
    # [activation gate]: True unless the verdict is PASS; Beta activation stays blocked otherwise.
    blocks_beta_activation: bool


def mask_discord_user_id(discord_user_id: object) -> str:
    """@description: Hide most of a Discord user identifier so verdicts can name a user without exposing the full ID.
    @param discord_user_id: Discord user identifier from the users or credit_ledger rows.
    @return: Identifier with only the fixed prefix and suffix visible; short identifiers are fully masked.
    """
    identifier_text = str(discord_user_id)
    visible_length = MASK_VISIBLE_PREFIX_LENGTH + MASK_VISIBLE_SUFFIX_LENGTH
    if len(identifier_text) <= visible_length:
        return MASK_CHARACTER * len(identifier_text)
    hidden_length = len(identifier_text) - visible_length
    prefix = identifier_text[:MASK_VISIBLE_PREFIX_LENGTH]
    suffix = identifier_text[len(identifier_text) - MASK_VISIBLE_SUFFIX_LENGTH:]
    return prefix + MASK_CHARACTER * hidden_length + suffix


def make_finding(rule_id: str, subject_kind: str, subject_id: object) -> Finding:
    """@description: Create a finding whose identifier is text and masked for users, so verdicts stay comparable and never expose full user IDs.
    @param rule_id: Rule identifier that detected the violation.
    @param subject_kind: Row kind the identifier refers to.
    @param subject_id: Row identifier from the reconciled table.
    @return: Immutable Finding record; user identifiers are masked.
    """
    if subject_kind == SUBJECT_USER:
        return Finding(rule_id, subject_kind, mask_discord_user_id(subject_id))
    return Finding(rule_id, subject_kind, str(subject_id))


def sum_ledger_deltas_by_user(ledger_rows: list) -> dict:
    """@description: Compute how much each user's credit is explained by ledger entries.
    @param ledger_rows: credit_ledger rows with discord_user_id and credit_delta.
    @return: Mapping from discord_user_id to the sum of its credit_delta values.
    """
    # [ledger totals]: Credit explained by ledger entries, keyed by Discord user identifier.
    ledger_totals: dict = {}
    for ledger_row in ledger_rows:
        owner_id = ledger_row["discord_user_id"]
        ledger_totals[owner_id] = ledger_totals.get(owner_id, UNEXPLAINED_BASELINE_BALANCE) + ledger_row["credit_delta"]
    return ledger_totals


def check_balance_matches_ledger(user_rows: list, ledger_rows: list) -> list:
    """@description: Block any user whose stored balance is not exactly explained by the ledger, which keeps the spend ledger authoritative.
    @param user_rows: users rows with discord_user_id and credit_balance.
    @param ledger_rows: credit_ledger rows used to explain each balance.
    @return: LED-F1 findings, one per user whose balance differs from its ledger sum.
    """
    ledger_totals = sum_ledger_deltas_by_user(ledger_rows)
    findings = []
    for user_row in user_rows:
        owner_id = user_row["discord_user_id"]
        explained_balance = ledger_totals.get(owner_id, UNEXPLAINED_BASELINE_BALANCE)
        if user_row["credit_balance"] != explained_balance:
            findings.append(make_finding(RULE_BALANCE_MATCHES_LEDGER, SUBJECT_USER, owner_id))
    return findings


def check_award_claims(ledger_rows: list, claim_rows: list) -> list:
    """@description: Ensure every submission award has a single claim so awards cannot exist without the claim trail.
    @param ledger_rows: credit_ledger rows whose reason determines whether a claim is required.
    @param claim_rows: jb2_claims rows linked by ledger_id.
    @return: LED-F2 findings, one per submission_award ledger row without exactly one claim.
    """
    claim_counts = Counter(claim_row["ledger_id"] for claim_row in claim_rows)
    findings = []
    for ledger_row in ledger_rows:
        if ledger_row["reason"] != REASON_SUBMISSION_AWARD:
            continue
        if claim_counts[ledger_row["ledger_id"]] != REQUIRED_CLAIMS_PER_AWARD:
            findings.append(make_finding(RULE_AWARD_HAS_ONE_CLAIM, SUBJECT_LEDGER, ledger_row["ledger_id"]))
    return findings


def check_claim_users(ledger_rows: list, claim_rows: list) -> list:
    """@description: Confirm each claim belongs to the same user as its ledger row so claims cannot be credited to another user.
    @param ledger_rows: credit_ledger rows providing the expected owner for each ledger_id.
    @param claim_rows: jb2_claims rows to compare against their ledger owner.
    @return: LED-F3 findings, one per claim whose owner differs from its ledger row; orphan claims are left to LED-F4.
    """
    ledger_owners = {ledger_row["ledger_id"]: ledger_row["discord_user_id"] for ledger_row in ledger_rows}
    findings = []
    for claim_row in claim_rows:
        ledger_id = claim_row["ledger_id"]
        if ledger_id not in ledger_owners:
            continue
        if claim_row["discord_user_id"] != ledger_owners[ledger_id]:
            findings.append(make_finding(RULE_CLAIM_USER_MATCHES_LEDGER, SUBJECT_CLAIM, claim_row["version_id"]))
    return findings


def find_orphan_ledger_findings(user_rows: list, ledger_rows: list) -> list:
    """@description: Report ledger rows whose owning user row is missing, since such credit has no account to belong to.
    @param user_rows: users rows that define the known Discord user identifiers.
    @param ledger_rows: credit_ledger rows to test for a matching user.
    @return: LED-F4 findings for ledger rows without a user.
    """
    known_user_ids = {user_row["discord_user_id"] for user_row in user_rows}
    return [
        make_finding(RULE_NO_ORPHAN_ROWS, SUBJECT_LEDGER, ledger_row["ledger_id"])
        for ledger_row in ledger_rows
        if ledger_row["discord_user_id"] not in known_user_ids
    ]


def find_orphan_claim_findings(ledger_rows: list, claim_rows: list) -> list:
    """@description: Report claims whose ledger row is missing, since a claim without its award breaks the audit trail.
    @param ledger_rows: credit_ledger rows that claims must reference.
    @param claim_rows: jb2_claims rows to test for a matching ledger row.
    @return: LED-F4 findings for claim rows without a ledger row.
    """
    known_ledger_ids = {ledger_row["ledger_id"] for ledger_row in ledger_rows}
    return [
        make_finding(RULE_NO_ORPHAN_ROWS, SUBJECT_CLAIM, claim_row["version_id"])
        for claim_row in claim_rows
        if claim_row["ledger_id"] not in known_ledger_ids
    ]


def check_orphan_rows(user_rows: list, ledger_rows: list, claim_rows: list) -> list:
    """@description: Detect orphan ledger and claim rows so reconciliation does not overlook rows that FK constraints should have prevented.
    @param user_rows: users rows.
    @param ledger_rows: credit_ledger rows.
    @param claim_rows: jb2_claims rows.
    @return: LED-F4 findings for every orphan ledger row and orphan claim row.
    """
    return find_orphan_ledger_findings(user_rows, ledger_rows) + find_orphan_claim_findings(ledger_rows, claim_rows)


def check_negative_balances(user_rows: list) -> list:
    """@description: Flag negative balances so credit can never be spent below zero even if the CHECK constraint is missing.
    @param user_rows: users rows with credit_balance.
    @return: LED-F5 findings, one per user whose balance is below the floor.
    """
    return [
        make_finding(RULE_BALANCE_NOT_NEGATIVE, SUBJECT_USER, user_row["discord_user_id"])
        for user_row in user_rows
        if user_row["credit_balance"] < MINIMUM_CREDIT_BALANCE
    ]


def count_findings_by_rule(findings: tuple) -> dict:
    """@description: Summarise findings per rule so the Owner sees how many rows each rule blocked.
    @param findings: Ordered findings from every rule.
    @return: Mapping of every evaluated rule identifier to its finding count, zeros included.
    """
    rule_counts = Counter(finding.rule_id for finding in findings)
    return {rule_id: rule_counts.get(rule_id, 0) for rule_id in EVALUATED_RULE_IDS}


def require_columns(rows: list, required_columns: tuple, table_name: str) -> None:
    """@description: Refuse snapshot rows that lack a required column, so missing evidence can never turn into PASS.
    @param rows: Rows of one snapshot table.
    @param required_columns: Column names the rules read from that table.
    @param table_name: Table name used in the error message; values are never echoed.
    @return: None; raises IncompleteLedgerRowError when any row misses a column.
    """
    for row in rows:
        missing_columns = [column for column in required_columns if column not in row]
        if missing_columns:
            raise IncompleteLedgerRowError(f"{table_name} row missing columns: {', '.join(missing_columns)}")


def reject_duplicate_keys(rows: list, key_column: str, table_name: str) -> None:
    """@description: Refuse snapshots that repeat a primary key, since duplicates would double-count balances or claims.
    @param rows: Rows of one snapshot table.
    @param key_column: Primary key column of that table.
    @param table_name: Table name used in the error message.
    @return: None; raises DuplicateLedgerRowError when a key repeats.
    """
    keys = [row[key_column] for row in rows]
    if len(set(keys)) != len(keys):
        raise DuplicateLedgerRowError(f"{table_name} snapshot repeats {key_column}")


def validate_snapshot(user_rows: list, ledger_rows: list, claim_rows: list) -> None:
    """@description: Run every structural check before rules execute, so invalid snapshots fail loudly.
    @param user_rows: users rows of the snapshot.
    @param ledger_rows: credit_ledger rows of the snapshot.
    @param claim_rows: jb2_claims rows of the snapshot.
    @return: None; raises an error for an empty users snapshot, missing columns or duplicate keys.
    """
    require_columns(user_rows, USER_REQUIRED_COLUMNS, "users")
    require_columns(ledger_rows, LEDGER_REQUIRED_COLUMNS, "credit_ledger")
    require_columns(claim_rows, CLAIM_REQUIRED_COLUMNS, "jb2_claims")
    reject_duplicate_keys(user_rows, "discord_user_id", "users")
    reject_duplicate_keys(ledger_rows, "ledger_id", "credit_ledger")
    reject_duplicate_keys(claim_rows, "version_id", "jb2_claims")


def build_inconclusive_verdict() -> LedgerIntegrityVerdict:
    """@description: Report an empty users snapshot as inconclusive, which blocks activation without claiming corruption or passing.
    @param: none
    @return: Verdict with no findings, zero counts, no identifiers, and blocks_beta_activation set to True.
    """
    return LedgerIntegrityVerdict(
        STATUS_INCONCLUSIVE_EMPTY_SNAPSHOT, EVALUATED_RULE_IDS, (), count_findings_by_rule(()), True
    )


def evaluate_ledger_integrity(user_rows: list, ledger_rows: list, claim_rows: list) -> LedgerIntegrityVerdict:
    """@description: Produce the single PASS or BLOCKED verdict that gates Private Beta database writes.
    @param user_rows: users rows read from one reconciliation snapshot.
    @param ledger_rows: credit_ledger rows from the same snapshot.
    @param claim_rows: jb2_claims rows from the same snapshot.
    @return: Verdict listing evaluated rules, findings and counts; inputs are never modified; invalid snapshots raise instead.
    """
    if not user_rows:
        return build_inconclusive_verdict()
    validate_snapshot(user_rows, ledger_rows, claim_rows)
    findings = tuple(
        check_balance_matches_ledger(user_rows, ledger_rows)
        + check_award_claims(ledger_rows, claim_rows)
        + check_claim_users(ledger_rows, claim_rows)
        + check_orphan_rows(user_rows, ledger_rows, claim_rows)
        + check_negative_balances(user_rows)
    )
    status = STATUS_PASS if not findings else STATUS_BLOCKED
    return LedgerIntegrityVerdict(
        status, EVALUATED_RULE_IDS, findings, count_findings_by_rule(findings), status != STATUS_PASS
    )

