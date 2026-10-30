"""Exercise the ledger integrity rules LED-F1 through LED-F6 with synthetic in-memory rows.

Tests check verdicts, identifiers and the Blocked boundary, and statically verify the read-only SQL against the schema text. No database, socket or Discord connection is used.
"""

import copy
import re
from pathlib import Path

import pytest

from bot.ledger_integrity import (
    EVALUATED_RULE_IDS,
    REASON_SUBMISSION_AWARD,
    RULE_AWARD_HAS_ONE_CLAIM,
    RULE_BALANCE_MATCHES_LEDGER,
    RULE_BALANCE_NOT_NEGATIVE,
    RULE_CLAIM_USER_MATCHES_LEDGER,
    RULE_NO_ORPHAN_ROWS,
    STATUS_BLOCKED,
    STATUS_PASS,
    SUBJECT_CLAIM,
    SUBJECT_LEDGER,
    SUBJECT_USER,
    SQL_BALANCE_VS_LEDGER,
    SQL_AWARD_WITHOUT_CLAIM,
    SQL_CLAIM_USER_MISMATCH,
    SQL_ORPHAN_LEDGER,
    SQL_NEGATIVE_BALANCE,
    Finding,
    check_award_claims,
    check_balance_matches_ledger,
    check_claim_users,
    check_negative_balances,
    check_orphan_rows,
    DuplicateLedgerRowError,
    STATUS_INCONCLUSIVE_EMPTY_SNAPSHOT,
    IncompleteLedgerRowError,
    evaluate_ledger_integrity,
    mask_discord_user_id,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "bot" / "database" / "postgres" / "0001_initial_schema.sql"
MIGRATION_002_PATH = SCHEMA_PATH  # [PostgreSQL]: The former MySQL migration 002 tables (jb2_claims) are declared in schema 0001.
SPEC_PATH = REPO_ROOT / "docs" / "recovery-batchb-private-beta-spec.md"

# [synthetic balance]: Distinctive value used to prove verdicts never echo balance amounts.
SECRET_LOOKING_BALANCE = 987654321


def user(discord_user_id: int, credit_balance: int) -> dict:
    """@description: Build a synthetic users row for rule tests.
    @param discord_user_id: Synthetic Discord user identifier.
    @param credit_balance: Stored users.credit_balance value.
    @return: Row mapping with the columns the rules read.
    """
    return {"discord_user_id": discord_user_id, "credit_balance": credit_balance}


def ledger(ledger_id: int, discord_user_id: int, credit_delta: int, reason: str = REASON_SUBMISSION_AWARD) -> dict:
    """@description: Build a synthetic credit_ledger row for rule tests.
    @param ledger_id: Synthetic ledger identifier.
    @param discord_user_id: Ledger owner's Discord user identifier.
    @param credit_delta: Signed credit change recorded by the ledger row.
    @param reason: credit_ledger reason value, defaulting to a submission award.
    @return: Row mapping with the columns the rules read.
    """
    return {
        "ledger_id": ledger_id,
        "discord_user_id": discord_user_id,
        "credit_delta": credit_delta,
        "reason": reason,
    }


def claim(version_id: str, ledger_id: int, discord_user_id: int) -> dict:
    """@description: Build a synthetic jb2_claims row for rule tests.
    @param version_id: Synthetic jb2_claims primary key.
    @param ledger_id: Ledger row the claim points to.
    @param discord_user_id: Claim owner's Discord user identifier.
    @return: Row mapping with the columns the rules read.
    """
    return {"version_id": version_id, "ledger_id": ledger_id, "discord_user_id": discord_user_id}


def consistent_dataset() -> tuple[list, list, list]:
    """@description: Provide one user whose balance, ledger and claims all agree.
    @param: none
    @return: Tuple of users, ledger and claims rows that should yield PASS.
    """
    users = [user(42, 2)]
    ledger_rows = [ledger(1, 42, 1), ledger(2, 42, 1)]
    claim_rows = [claim("v-1", 1, 42), claim("v-2", 2, 42)]
    return users, ledger_rows, claim_rows


# ---- Identifier masking (LED-A3): full Discord user IDs never reach the verdict ----

LONG_DISCORD_USER_ID = 123456789012345678


def test_mask_keeps_only_fixed_prefix_and_suffix():
    """@description: A long Discord user ID keeps two leading and two trailing digits and masks the middle.
    @param: none
    @return: None.
    """
    assert mask_discord_user_id(LONG_DISCORD_USER_ID) == "12**************78"


def test_mask_fully_hides_short_identifiers():
    """@description: Identifiers too short for prefix and suffix are fully masked, never shown whole.
    @param: none
    @return: None.
    """
    assert mask_discord_user_id(42) == "**"
    assert mask_discord_user_id(7) == "*"


def test_verdict_never_contains_full_user_id():
    """@description: A blocked verdict for a long user ID exposes only the masked identifier.
    @param: none
    @return: None.
    """
    verdict = evaluate_ledger_integrity([user(LONG_DISCORD_USER_ID, 5)], [], [])
    assert verdict.status == STATUS_BLOCKED
    assert verdict.findings == (Finding(RULE_BALANCE_MATCHES_LEDGER, SUBJECT_USER, "12**************78"),)
    assert str(LONG_DISCORD_USER_ID) not in repr(verdict)


def test_masking_does_not_hide_ledger_or_claim_identifiers():
    """@description: Only user identifiers are masked; ledger and claim identifiers remain visible for audit follow-up.
    @param: none
    @return: None.
    """
    findings = check_award_claims([ledger(1, LONG_DISCORD_USER_ID, 1)], [])
    assert findings == [Finding(RULE_AWARD_HAS_ONE_CLAIM, SUBJECT_LEDGER, "1")]


# ---- LED-F1: balance equals ledger sum (LED-F6: no opening-balance allowance) ----

def test_f1_matching_balance_yields_no_finding():
    """@description: A balance equal to the ledger sum must not be reported.
    @param: none
    @return: None.
    """
    findings = check_balance_matches_ledger([user(42, 3)], [ledger(1, 42, 1), ledger(2, 42, 1), ledger(3, 42, 1)])
    assert findings == []


def test_f1_mismatched_balance_reports_user_id_only():
    """@description: A mismatched balance must block with the user identifier and no amounts.
    @param: none
    @return: None.
    """
    findings = check_balance_matches_ledger([user(42, SECRET_LOOKING_BALANCE)], [ledger(1, 42, 1)])
    assert findings == [Finding(RULE_BALANCE_MATCHES_LEDGER, SUBJECT_USER, "**")]


def test_f1_user_without_ledger_rows_counts_as_zero():
    """@description: A user with zero balance and no ledger rows is consistent.
    @param: none
    @return: None.
    """
    assert check_balance_matches_ledger([user(7, 0)], []) == []


def test_f6_unexplained_balance_without_ledger_is_never_accepted():
    """@description: A nonzero balance without ledger rows is an unexplained balance, never an opening balance.
    @param: none
    @return: None.
    """
    verdict = evaluate_ledger_integrity([user(7, 5)], [], [])
    assert verdict.status == STATUS_BLOCKED
    assert verdict.findings == (Finding(RULE_BALANCE_MATCHES_LEDGER, SUBJECT_USER, "*"),)


# ---- LED-F2: each submission_award ledger row has exactly one claim ----

def test_f2_award_with_exactly_one_claim_yields_no_finding():
    """@description: A submission award linked to one claim is consistent.
    @param: none
    @return: None.
    """
    assert check_award_claims([ledger(1, 42, 1)], [claim("v-1", 1, 42)]) == []


def test_f2_award_without_claim_reports_ledger_id():
    """@description: A submission award with no claim must block with the ledger identifier.
    @param: none
    @return: None.
    """
    findings = check_award_claims([ledger(1, 42, 1)], [])
    assert findings == [Finding(RULE_AWARD_HAS_ONE_CLAIM, SUBJECT_LEDGER, "1")]


def test_f2_award_with_two_claims_reports_ledger_id():
    """@description: Two claims for one award must block even though the schema should prevent it.
    @param: none
    @return: None.
    """
    findings = check_award_claims([ledger(1, 42, 1)], [claim("v-1", 1, 42), claim("v-2", 1, 42)])
    assert findings == [Finding(RULE_AWARD_HAS_ONE_CLAIM, SUBJECT_LEDGER, "1")]


def test_f2_non_award_reason_does_not_require_claim():
    """@description: Only submission_award ledger rows must have a claim.
    @param: none
    @return: None.
    """
    assert check_award_claims([ledger(1, 42, -1, "exchange_debit")], []) == []


# ---- LED-F3: claim user equals ledger user ----

def test_f3_matching_claim_user_yields_no_finding():
    """@description: A claim owned by the same user as its ledger row is consistent.
    @param: none
    @return: None.
    """
    assert check_claim_users([ledger(1, 42, 1)], [claim("v-1", 1, 42)]) == []


def test_f3_claim_user_mismatch_reports_claim_version():
    """@description: A claim owned by a different user must block with the claim identifier.
    @param: none
    @return: None.
    """
    findings = check_claim_users([ledger(1, 42, 1)], [claim("v-1", 1, 99)])
    assert findings == [Finding(RULE_CLAIM_USER_MATCHES_LEDGER, SUBJECT_CLAIM, "v-1")]


# ---- LED-F4: no orphan ledger or claim rows ----

def test_f4_ledger_without_user_reports_ledger_id():
    """@description: A ledger row whose user is absent is an orphan and must block.
    @param: none
    @return: None.
    """
    findings = check_orphan_rows([], [ledger(5, 42, 1, "exchange_debit")], [])
    assert findings == [Finding(RULE_NO_ORPHAN_ROWS, SUBJECT_LEDGER, "5")]


def test_f4_claim_without_ledger_reports_claim_version():
    """@description: A claim whose ledger row is absent is an orphan and must block.
    @param: none
    @return: None.
    """
    findings = check_orphan_rows([user(42, 0)], [], [claim("v-9", 77, 42)])
    assert findings == [Finding(RULE_NO_ORPHAN_ROWS, SUBJECT_CLAIM, "v-9")]


# ---- LED-F5: no negative balance ----

def test_f5_non_negative_balance_yields_no_finding():
    """@description: A zero balance is allowed by the non-negative rule.
    @param: none
    @return: None.
    """
    assert check_negative_balances([user(42, 0)]) == []


def test_f5_negative_balance_reports_user_id_only():
    """@description: A negative balance must block with the user identifier and no amount.
    @param: none
    @return: None.
    """
    findings = check_negative_balances([user(42, -1)])
    assert findings == [Finding(RULE_BALANCE_NOT_NEGATIVE, SUBJECT_USER, "**")]


# ---- Verdict boundary ----

def test_verdict_zero_findings_passes_and_evaluates_all_five_rules():
    """@description: A consistent dataset passes only after all five rules were evaluated.
    @param: none
    @return: None.
    """
    verdict = evaluate_ledger_integrity(*consistent_dataset())
    assert verdict.status == STATUS_PASS
    assert verdict.blocks_beta_activation is False
    assert verdict.evaluated_rule_ids == EVALUATED_RULE_IDS
    assert EVALUATED_RULE_IDS == (
        RULE_BALANCE_MATCHES_LEDGER,
        RULE_AWARD_HAS_ONE_CLAIM,
        RULE_CLAIM_USER_MATCHES_LEDGER,
        RULE_NO_ORPHAN_ROWS,
        RULE_BALANCE_NOT_NEGATIVE,
    )
    assert verdict.findings == ()
    assert set(verdict.finding_counts.values()) == {0}


def test_empty_user_snapshot_is_inconclusive_and_blocks_activation():
    """@description: An empty users snapshot is INCONCLUSIVE_EMPTY_SNAPSHOT: never PASS, never BLOCKED corruption, zero counts, no identifiers, and it still blocks Beta activation.
    @param: none
    @return: None.
    """
    verdict = evaluate_ledger_integrity([], [], [])
    assert verdict.status == STATUS_INCONCLUSIVE_EMPTY_SNAPSHOT
    assert verdict.blocks_beta_activation is True
    assert verdict.findings == ()
    assert set(verdict.finding_counts.values()) == {0}


def test_missing_required_field_raises_instead_of_passing():
    """@description: A row missing a required column must raise an explicit error, never produce a PASS.
    @param: none
    @return: None.
    """
    incomplete_users = [{"discord_user_id": 42}]
    with pytest.raises(IncompleteLedgerRowError):
        evaluate_ledger_integrity(incomplete_users, [], [])


def test_duplicate_user_rows_raise_instead_of_double_counting():
    """@description: Duplicate users rows for one Discord user must raise, since they would double-count the balance check.
    @param: none
    @return: None.
    """
    with pytest.raises(DuplicateLedgerRowError):
        evaluate_ledger_integrity([user(42, 0), user(42, 0)], [], [])


def test_verdict_single_finding_blocks_with_counts_and_identifiers_only():
    """@description: Any finding blocks, and the verdict exposes counts and identifiers but no balance values.
    @param: none
    @return: None.
    """
    users, ledger_rows, claim_rows = consistent_dataset()
    users = [user(42, SECRET_LOOKING_BALANCE)]
    verdict = evaluate_ledger_integrity(users, ledger_rows, claim_rows)
    assert verdict.status == STATUS_BLOCKED
    assert verdict.findings == (Finding(RULE_BALANCE_MATCHES_LEDGER, SUBJECT_USER, "**"),)
    assert verdict.finding_counts[RULE_BALANCE_MATCHES_LEDGER] == 1
    assert str(SECRET_LOOKING_BALANCE) not in repr(verdict)


def test_verdict_does_not_mutate_or_repair_inputs():
    """@description: Evaluation must never repair balances, add ledger rows or alter claims.
    @param: none
    @return: None.
    """
    users = [user(42, SECRET_LOOKING_BALANCE), user(7, -3)]
    ledger_rows = [ledger(1, 42, 1), ledger(2, 99, 1)]
    claim_rows = [claim("v-1", 1, 42), claim("v-2", 5, 42)]
    snapshot = copy.deepcopy((users, ledger_rows, claim_rows))
    verdict = evaluate_ledger_integrity(users, ledger_rows, claim_rows)
    assert verdict.status == STATUS_BLOCKED
    assert (users, ledger_rows, claim_rows) == snapshot


# ---- LED-A4/A5: amounts never leave the process, on success, failure and error paths ----

SENTINEL_AMOUNT_TEXT = str(SECRET_LOOKING_BALANCE)
SENTINEL_USER_ID_TEXT = str(LONG_DISCORD_USER_ID)


def assert_no_sentinel_leak(output_text: str) -> None:
    """@description: Fail if a sentinel balance or full user ID appears in any output text.
    @param output_text: Text produced by a verdict, finding or exception.
    @return: None.
    """
    assert SENTINEL_AMOUNT_TEXT not in output_text
    assert SENTINEL_USER_ID_TEXT not in output_text


def test_no_leak_on_pass_path():
    """@description: A PASS verdict built from sentinel balances must not echo amounts or full user IDs.
    @param: none
    @return: None.
    """
    users = [user(LONG_DISCORD_USER_ID, SECRET_LOOKING_BALANCE)]
    ledger_rows = [ledger(1, LONG_DISCORD_USER_ID, SECRET_LOOKING_BALANCE)]
    claim_rows = [claim("v-1", 1, LONG_DISCORD_USER_ID)]
    verdict = evaluate_ledger_integrity(users, ledger_rows, claim_rows)
    assert verdict.status == STATUS_PASS
    assert_no_sentinel_leak(repr(verdict))
    assert_no_sentinel_leak(str(verdict))


def test_no_leak_on_blocked_path_and_findings():
    """@description: A BLOCKED verdict and each finding built from sentinel values must expose only masked identifiers.
    @param: none
    @return: None.
    """
    verdict = evaluate_ledger_integrity([user(LONG_DISCORD_USER_ID, SECRET_LOOKING_BALANCE)], [], [])
    assert verdict.status == STATUS_BLOCKED
    assert_no_sentinel_leak(repr(verdict))
    assert_no_sentinel_leak(str(verdict))
    for finding in verdict.findings:
        assert_no_sentinel_leak(repr(finding))


def test_no_leak_in_raised_errors():
    """@description: Each error raised by snapshot validation must omit sentinel amounts and full user IDs in str and repr.
    @param: none
    @return: None.
    """
    incomplete_ledger = [{"ledger_id": 1, "discord_user_id": LONG_DISCORD_USER_ID, "reason": "submission_award"}]
    duplicate_users = [user(LONG_DISCORD_USER_ID, SECRET_LOOKING_BALANCE), user(LONG_DISCORD_USER_ID, SECRET_LOOKING_BALANCE)]
    failing_calls = [
        lambda: evaluate_ledger_integrity([user(LONG_DISCORD_USER_ID, SECRET_LOOKING_BALANCE)], incomplete_ledger, []),
        lambda: evaluate_ledger_integrity(duplicate_users, [], []),
    ]
    for failing_call in failing_calls:
        with pytest.raises(ValueError) as raised:
            failing_call()
        assert_no_sentinel_leak(str(raised.value))
        assert_no_sentinel_leak(repr(raised.value))


# ---- Static SQL checks (read-only text, never executed) ----

SQL_CONSTANTS = {
    "LED-F1": SQL_BALANCE_VS_LEDGER,
    "LED-F2": SQL_AWARD_WITHOUT_CLAIM,
    "LED-F3": SQL_CLAIM_USER_MISMATCH,
    "LED-F4": SQL_ORPHAN_LEDGER,
    "LED-F5": SQL_NEGATIVE_BALANCE,
}
FORBIDDEN_SQL_WORDS = r"\b(INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER|CREATE|TRUNCATE|GRANT|CALL)\b"
SQL_TABLE_REFERENCE = r"(?:FROM|JOIN)\s+(\w+)\s+AS\s+(\w+)"
SQL_COLUMN_REFERENCE = r"\b(\w+)\.(\w+)\b"
NON_COLUMN_WORDS = {"constraint", "index", "primary", "unique", "key", "foreign", "references", "check"}


def strip_sql_comments(sql_text: str) -> str:
    """@description: Remove line comments so only declarations are parsed.
    @param sql_text: Raw SQL file text.
    @return: SQL text without `--` line comments.
    """
    return re.sub(r"--[^\n]*", "", sql_text)


def table_columns(sql_text: str, table_name: str) -> set[str]:
    """@description: Extract declared column names of one CREATE TABLE block as static evidence.
    @param sql_text: Raw SQL file text containing the table declaration.
    @param table_name: Table whose declared columns are needed.
    @return: Set of lowercase column names; constraints and keywords are excluded.
    """
    pattern = rf"CREATE TABLE {table_name}\s*\((.*?)\n\);"  # [block]: Each PostgreSQL table body ends with a line holding only ");".
    match = re.search(pattern, strip_sql_comments(sql_text), flags=re.DOTALL)
    assert match, table_name
    columns = set()
    for line in match.group(1).splitlines():
        first_word = re.match(r"\s*([A-Za-z_]\w*)\s", line)
        if first_word and first_word.group(1).lower() not in NON_COLUMN_WORDS:
            columns.add(first_word.group(1).lower())
    return columns


def known_schema() -> dict[str, set[str]]:
    """@description: Map the reconciliation tables to columns declared in the approved schema files.
    @param: none
    @return: Table name mapped to its declared column set.
    """
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    migration_text = MIGRATION_002_PATH.read_text(encoding="utf-8")
    return {
        "users": table_columns(schema_text, "users"),
        "credit_ledger": table_columns(schema_text, "credit_ledger"),
        "jb2_claims": table_columns(migration_text, "jb2_claims"),
    }


@pytest.mark.parametrize("rule_name", sorted(SQL_CONSTANTS))
def test_design_level_sql_text_is_select_only_and_unparameterised(rule_name):
    """@description: DESIGN-LEVEL static text check; it does not prove the SQL executes correctly. Each reconciliation statement must be a read-only SELECT with no interpolation markers.
    @param rule_name: Rule identifier whose SQL constant is checked.
    @return: None.
    """
    statement = SQL_CONSTANTS[rule_name]
    assert statement.lstrip().upper().startswith("SELECT")
    assert not re.search(FORBIDDEN_SQL_WORDS, statement, flags=re.IGNORECASE)
    assert not re.search(r"[%{]", statement)
    assert ";" not in statement.strip().rstrip(";")


def select_list(statement: str) -> str:
    """@description: Return the text between the first SELECT and FROM, as static evidence of the output columns.
    @param statement: One read-only SQL statement.
    @return: The select list text without line breaks.
    """
    match = re.search(r"SELECT\s+(.*?)\s+FROM\b", statement, flags=re.DOTALL | re.IGNORECASE)
    assert match
    return " ".join(match.group(1).split())


def test_design_level_sql_select_lists_expose_only_identifiers():
    """@description: DESIGN-LEVEL static text check of LED-A4; it does not prove the SQL executes or returns the rows described. Select lists must carry identifiers and never balance or ledger-sum amounts.
    @param: none
    @return: None.
    """
    assert select_list(SQL_BALANCE_VS_LEDGER) == "u.discord_user_id"
    assert select_list(SQL_NEGATIVE_BALANCE) == "discord_user_id"
    for statement in SQL_CONSTANTS.values():
        listed_columns = select_list(statement)
        for amount_column in ("credit_balance", "credit_delta", "ledger_total", "SUM"):
            assert amount_column not in listed_columns


@pytest.mark.parametrize("rule_name", sorted(SQL_CONSTANTS))
def test_design_level_sql_text_matches_spec_section_3_6(rule_name):
    """@description: DESIGN-LEVEL static text check; it does not prove the SQL executes correctly. Keep the SQL constants identical to the approved draft so changes are deliberate.
    @param rule_name: Rule identifier whose SQL constant is compared with the spec.
    @return: None.
    """
    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    normalised_spec = " ".join(spec_text.split())
    assert " ".join(SQL_CONSTANTS[rule_name].split()) in normalised_spec


SQL_KEYWORDS = {"select", "from", "left", "join", "on", "group", "by", "having", "where", "and", "as", "coalesce", "sum", "is", "null", "not"}


def referenced_tables(statement: str) -> dict[str, str]:
    """@description: Map each alias (or bare table name) used in the statement to its table, as static evidence.
    @param statement: One read-only SQL statement.
    @return: Mapping from alias or table name to the real table name.
    """
    tables = {}
    for table, alias in re.findall(SQL_TABLE_REFERENCE, statement, flags=re.IGNORECASE):
        tables[alias] = table
    for table in re.findall(r"(?:FROM|JOIN)\s+(\w+)\b(?!\s+AS\b)", statement, flags=re.IGNORECASE):
        tables[table] = table
    return tables


def unresolved_identifiers(statement: str, schema: dict[str, set[str]]) -> set[str]:
    """@description: Find bare identifiers that match no declared column, alias, output name or SQL keyword.
    @param statement: One read-only SQL statement with string literals allowed.
    @param schema: Table name mapped to its declared columns.
    @return: Lowercase identifiers that cannot be resolved against the static schema.
    """
    without_literals = re.sub(r"'[^']*'", "", statement)
    tables = referenced_tables(without_literals)
    known = set(SQL_KEYWORDS) | {name.lower() for name in tables} | {name.lower() for name in tables.values()}
    known |= {name.lower() for name in re.findall(r"\bAS\s+(\w+)", without_literals, flags=re.IGNORECASE)}
    for table in set(tables.values()):
        known |= schema[table]
    tokens = re.findall(r"(?<![.\w])([A-Za-z_]\w*)\b", without_literals)
    return {token.lower() for token in tokens} - known


@pytest.mark.parametrize("rule_name", sorted(SQL_CONSTANTS))
def test_design_level_sql_references_exist_in_schema_text(rule_name):
    """@description: DESIGN-LEVEL static text check; it does not prove the SQL executes correctly. Every table, alias.column and bare column the SQL uses must exist in the static schema declarations.
    @param rule_name: Rule identifier whose SQL constant is checked against the schema.
    @return: None.
    """
    statement = SQL_CONSTANTS[rule_name]
    schema = known_schema()
    tables = referenced_tables(statement)
    assert tables, rule_name
    for table in set(tables.values()):
        assert table in schema, table
    for alias, column in re.findall(SQL_COLUMN_REFERENCE, statement):
        assert alias in tables, alias
        assert column.lower() in schema[tables[alias]], f"{tables[alias]}.{column}"
    assert unresolved_identifiers(statement, schema) == set()


def test_design_level_jb2_claims_declarations_in_migration_text():
    """@description: DESIGN-LEVEL static text check; it does not prove the SQL executes correctly. Lock the spec fact that jb2_claims is keyed by version_id with ledger_id UNIQUE and no claim_id.
    @param: none
    @return: None.
    """
    migration_text = strip_sql_comments(MIGRATION_002_PATH.read_text(encoding="utf-8"))
    claims_block = re.search(r"CREATE TABLE jb2_claims\s*\((.*?)\n\);", migration_text, flags=re.DOTALL).group(1)
    assert re.search(r"version_id\s+VARCHAR\(128\)\s+COLLATE \"C\"\s+NOT NULL PRIMARY KEY", claims_block)
    assert re.search(r"ledger_id\s+BIGINT NOT NULL UNIQUE", claims_block)
    assert "claim_id" not in known_schema()["jb2_claims"]
    assert "claim_id" not in migration_text


def test_design_level_reason_enum_text_in_schema():
    """@description: DESIGN-LEVEL static text check; it does not prove the SQL executes correctly. The award reason used by LED-F2 must be a declared credit_ledger reason.
    @param: none
    @return: None.
    """
    schema_text = strip_sql_comments(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert f"'{REASON_SUBMISSION_AWARD}'" in schema_text
