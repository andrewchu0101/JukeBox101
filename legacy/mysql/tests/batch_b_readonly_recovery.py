"""Offline guards for the Batch B Recovery read-only verification path.

Compares the requested MySQL target, admits only a single SELECT statement, builds redacted summaries and REC-A9 count statements, and decides the verdict from already-collected facts.
It performs no database or network I/O; callers collect facts elsewhere and pass them in.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

AUTHORIZED_RECOVERY_HOST = "127.0.0.1"
AUTHORIZED_RECOVERY_PORT = 3306
AUTHORIZED_RECOVERY_DATABASE = "jukebox101_test"
# [forbidden accounts]: Existing application, migrator, and harness accounts that must never serve as the Recovery read-only account.
FORBIDDEN_READONLY_ACCOUNTS = frozenset({"jukebox101_test_app", "jukebox101_test_migrator", "jukebox101_test_harness"})
AUTHORIZED_TARGET_VALUES = {
    "host": AUTHORIZED_RECOVERY_HOST,
    "port": AUTHORIZED_RECOVERY_PORT,
    "database": AUTHORIZED_RECOVERY_DATABASE,
}
# [statement markers]: Comment and statement-separator characters that could hide or append a second statement.
REJECTED_STATEMENT_MARKERS = ("/*", "--", "#", ";")
# [clause denylist]: SELECT clauses that write to files or take locks, so a SELECT-prefixed text can still mutate state.
REJECTED_SELECT_CLAUSE_KEYWORDS = frozenset({"INTO", "FOR", "LOCK"})
LEADING_KEYWORD_PATTERN = re.compile(r"\s*([A-Za-z_]+)")
KEYWORD_PATTERN = re.compile(r"[A-Za-z_]+")
PERMITTED_STATEMENT_KEYWORD = "SELECT"
# [provenance fields]: Text fields a reviewed baseline must name, so its source can be traced.
REQUIRED_PROVENANCE_TEXT_FIELDS = ("source_id", "approval_reference")
# [approved status]: The only provenance status that may stand as an approved baseline.
APPROVED_PROVENANCE_STATUS = "approved"

VERDICT_VERIFIED = "RECOVERY_VERIFIED_READONLY"
VERDICT_NOT_VERIFIED = "RECOVERY_NOT_VERIFIED"
# [reviewed baseline counts]: Base tables and FK edges the reviewed Batch B schema must expose to the read-only account.
EXPECTED_BASE_TABLE_COUNT = 23
EXPECTED_FK_EDGE_COUNT = 29
# [control precondition]: jb2_control values that must hold before Recovery may be verified (REC-F5).
EXPECTED_CONTROL_VALUES = {
    "accepted_sequence": 0,
    "revision": 0,
    "snapshot_ready": 0,
    "legacy_writers_quiesced": 0,
    "settlement_enabled": 0,
}
RECOVERY_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "docs" / "batch-b-recovery-manifest.json"
# [secret key markers]: Substrings that disqualify a key from any output summary (REC-F8).
SECRET_KEY_MARKERS = ("PASSWORD", "PASS", "TOKEN", "SECRET", "DSN")
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.#>-]{0,127}$")
SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
# [structural baseline]: AUTO_INCREMENT counters advance with rolled-back inserts and are not schema drift.
AUTO_INCREMENT_PATTERN = re.compile(r" AUTO_INCREMENT=\d+")
RESIDUAL_COUNT_TEMPLATE = "SELECT COUNT(*) AS residual_count FROM `{table}` WHERE `{column}` IN ({placeholders})"
REFERENCED_TABLE_INDEX = 4
EDGE_FIELD_COUNT = 6


@dataclass(frozen=True)
class TargetSpec:
    """Requested MySQL connection target that must match the authorized Recovery target before any connect.

    Collaborators: check_target; the caller supplies values from configuration, never from a default.
    """

    host: object
    port: object
    database: object
    account: object


@dataclass(frozen=True)
class TargetCheck:
    """Outcome of comparing a requested target with the authorized target.

    mismatched_fields lists every field that failed, so one report shows the full problem.
    """

    is_authorized: bool
    mismatched_fields: tuple


@dataclass(frozen=True)
class StatementCheck:
    """Outcome of the single-SELECT allowlist for one statement text.

    reason is a stable code, empty when allowed, so callers can count violations without parsing prose.
    """

    allowed: bool
    reason: str


class UnmappableReferenceError(ValueError):
    """Raised when a reviewed FK edge cannot be mapped to a deleted-PK residual count; the caller must stop instead of guessing."""


@dataclass(frozen=True)
class ReferenceResidualQuery:
    """One parameterized REC-A9 count for a reviewed FK edge into a deleted table.

    edge_id names the reviewed edge; sql contains only %s placeholders; params carries the deleted PK values.
    """

    edge_id: str
    sql: str
    params: tuple


@dataclass(frozen=True)
class ReadonlyVerificationOptions:
    """Gates for checks that are not yet owner-approved; both default to OFF so they never count as approved.

    positive_control_enabled controls REC-A10; reference_residual_enabled controls REC-A9.
    """

    positive_control_enabled: bool = False
    reference_residual_enabled: bool = False


@dataclass(frozen=True)
class ReadonlyFacts:
    """Facts collected by a caller through read-only queries; None marks a fact that could not be collected.

    The verdict compares these facts with the reviewed manifest and never queries MySQL itself.
    """

    target_check: TargetCheck
    statement_violation_count: Optional[int]
    schema_fingerprint: Optional[str]
    baseline_fingerprint: Optional[str]
    fk_edges: Optional[tuple]
    control_values: Optional[Mapping]
    applied_schema_version: Optional[str]
    table_row_counts: Optional[Mapping]
    expected_row_counts: Mapping
    deleted_pk_residual_counts: Optional[Mapping]
    base_table_count: Optional[int]
    fk_edge_count: Optional[int]
    reference_residual_counts: Optional[Mapping]
    # [fk rule evidence]: Observed ON UPDATE/ON DELETE per edge from REFERENTIAL_CONSTRAINTS; None when the caller could not read it.
    observed_fk_rules: Optional[Mapping]
    # [fk rule baseline]: Owner/DBA-approved ON UPDATE/ON DELETE per edge; the manifest does not carry these, so None until supplied.
    reviewed_fk_rules: Optional[Mapping]
    # [rule baseline label]: Caller-supplied name of the reviewed source; must not be the observed REFERENTIAL_CONSTRAINTS.
    reviewed_fk_rules_source: Optional[str]
    # [baseline provenance]: Caller-supplied record of source id, approval status and reference, and independence attestation for the reviewed baseline.
    reviewed_fk_provenance: Optional[Mapping]


def is_permitted_readonly_account(account: object) -> bool:
    """@description: Reject missing names and the existing application roles so Recovery never reuses a write-capable account.
    @param account: Requested read-only account name from configuration.
    @return: True when the name is non-empty text and not a forbidden application account.
    """
    if not isinstance(account, str) or not account.strip():
        return False
    return account.strip().lower() not in {name.lower() for name in FORBIDDEN_READONLY_ACCOUNTS}


def check_target(requested: TargetSpec) -> TargetCheck:
    """@description: Stop Recovery before connecting when host, port, database, or account differs from the authorized target.
    @param requested: Target fields the caller intends to connect to.
    @return: TargetCheck naming every mismatched field; is_authorized is True only when none differ.
    """
    mismatched_fields = [
        field_name
        for field_name, expected_value in AUTHORIZED_TARGET_VALUES.items()
        if not _same_typed_value(getattr(requested, field_name), expected_value)
    ]
    if not is_permitted_readonly_account(requested.account):
        mismatched_fields.append("account")
    return TargetCheck(is_authorized=not mismatched_fields, mismatched_fields=tuple(mismatched_fields))


def _same_typed_value(actual: object, expected: object) -> bool:
    """@description: Require exact type and value so a string port or a float host cannot pass as the authorized value.
    @param actual: Value supplied by the caller.
    @param expected: Authorized value to compare against.
    @return: True only when both the type and the value are identical.
    """
    return type(actual) is type(expected) and actual == expected


def check_statement(sql_text: object) -> StatementCheck:
    """@description: Admit only one read-only SELECT so no write, transaction control, or appended statement reaches MySQL.
    @param sql_text: Candidate statement text; any non-text input is rejected.
    @return: StatementCheck with allowed True only for a single SELECT without markers or write clauses.
    """
    if not isinstance(sql_text, str):
        return StatementCheck(allowed=False, reason="statement_not_text")
    if not sql_text.strip():
        return StatementCheck(allowed=False, reason="statement_empty")
    if any(marker in sql_text for marker in REJECTED_STATEMENT_MARKERS):
        return StatementCheck(allowed=False, reason="statement_marker_rejected")
    leading_match = LEADING_KEYWORD_PATTERN.match(sql_text)
    if leading_match is None or leading_match.group(1).upper() != PERMITTED_STATEMENT_KEYWORD:
        return StatementCheck(allowed=False, reason="first_keyword_not_select")
    keywords = {match.upper() for match in KEYWORD_PATTERN.findall(sql_text)}
    if keywords & REJECTED_SELECT_CLAUSE_KEYWORDS:
        return StatementCheck(allowed=False, reason="select_clause_rejected")
    return StatementCheck(allowed=True, reason="")


def redact_summary(values: Mapping) -> dict:
    """@description: Keep only counts, booleans, fingerprints, and identifiers so reports never carry secrets or full row values.
    @param values: Candidate summary entries assembled by the caller.
    @return: New dictionary without secret-named keys or non-summary values.
    """
    return {key: value for key, value in values.items() if _is_summary_key(key) and _is_summary_value(value)}


def _is_summary_key(key: object) -> bool:
    """@description: Block any key whose name contains a credential marker, so secrets cannot surface through a summary.
    @param key: Candidate summary key.
    @return: True when the key is text and contains no secret marker.
    """
    if not isinstance(key, str):
        return False
    upper_key = key.upper()
    return not any(marker in upper_key for marker in SECRET_KEY_MARKERS)


def _is_summary_value(value: object) -> bool:
    """@description: Allow only scalar summary shapes; free text is refused because it may be a row value.
    @param value: Candidate summary value.
    @return: True for integers, booleans, sha256 hex digests, and plain identifiers.
    """
    if isinstance(value, (bool, int)):
        return True
    if isinstance(value, str):
        return bool(FINGERPRINT_PATTERN.match(value) or SAFE_IDENTIFIER_PATTERN.match(value))
    return False


def compute_schema_fingerprint(table_definitions: Mapping) -> str:
    """@description: Reduce SHOW CREATE TABLE output to one structural hash so schema drift is detectable without storing definitions.
    @param table_definitions: Mapping from table name to its SHOW CREATE TABLE text.
    @return: Lowercase sha256 hex digest over the sorted, counter-free definitions.
    """
    if not table_definitions:
        raise ValueError("schema fingerprint requires at least one table definition")
    canonical_parts = [
        f"{table_name}\n{AUTO_INCREMENT_PATTERN.sub('', definition)}"
        for table_name, definition in sorted(table_definitions.items())
    ]
    return hashlib.sha256("\n--\n".join(canonical_parts).encode("utf-8")).hexdigest()


def load_recovery_manifest(manifest_path: Path = RECOVERY_MANIFEST_PATH) -> dict:
    """@description: Load the reviewed Batch B manifest that defines the expected FK edges, deleted PKs, and control values.
    @param manifest_path: Location of the reviewed manifest JSON file.
    @return: Parsed manifest dictionary, exactly as reviewed.
    """
    with Path(manifest_path).open(encoding="utf-8") as manifest_file:
        return json.load(manifest_file)


def build_reference_residual_queries(manifest: Mapping) -> tuple:
    """@description: Turn every reviewed FK edge into a deleted table into a parameterized COUNT, without executing any of them.
    @param manifest: Reviewed manifest holding reviewed_fk_column_edges and delete_manifest.
    @return: Tuple of ReferenceResidualQuery, one per edge into a deleted table; raises when any edge is unmappable.
    """
    delete_manifest = manifest["delete_manifest"]
    queries = []
    for edge in manifest["reviewed_fk_column_edges"]:
        if _split_edge(edge)[REFERENCED_TABLE_INDEX] in delete_manifest:
            queries.append(_build_residual_query(edge, delete_manifest))
    return tuple(queries)


def _split_edge(edge: Sequence) -> tuple:
    """@description: Check that a reviewed edge has the six fields the residual builder depends on, so malformed input stops early.
    @param edge: One reviewed edge as [referencing_table, referencing_column, ordinal, schema, referenced_table, referenced_column].
    @return: The six fields as a tuple.
    """
    if len(edge) != EDGE_FIELD_COUNT:
        raise UnmappableReferenceError(f"reviewed edge must have {EDGE_FIELD_COUNT} fields")
    return tuple(edge)


def _build_residual_query(edge: Sequence, delete_manifest: Mapping) -> ReferenceResidualQuery:
    """@description: Build one COUNT whose identifiers are validated and whose PK values travel only as parameters.
    @param edge: One reviewed edge into a deleted table.
    @param delete_manifest: Mapping from deleted table to its deleted PK rows.
    @return: ReferenceResidualQuery for the edge.
    """
    referencing_table, referencing_column, _ordinal, schema, referenced_table, referenced_column = _split_edge(edge)
    edge_id = edge_identifier(edge)
    _require_safe_identifiers(referencing_table, referencing_column, referenced_table, referenced_column)
    if schema != AUTHORIZED_RECOVERY_DATABASE:
        raise UnmappableReferenceError(f"edge from schema outside the authorized target: {edge_id}")
    pk_values = _deleted_pk_values(delete_manifest[referenced_table], referenced_column, edge_id)
    placeholders = ", ".join(["%s"] * len(pk_values))
    sql = RESIDUAL_COUNT_TEMPLATE.format(table=referencing_table, column=referencing_column, placeholders=placeholders)
    return ReferenceResidualQuery(edge_id=edge_id, sql=sql, params=pk_values)


def _require_safe_identifiers(*identifiers: str) -> None:
    """@description: Refuse any table or column name that is not a plain SQL identifier, because identifiers cannot be parameterized.
    @param identifiers: Table and column names taken from the reviewed manifest.
    @return: None; raises UnmappableReferenceError on the first unsafe name.
    """
    for identifier in identifiers:
        if not isinstance(identifier, str) or not SQL_IDENTIFIER_PATTERN.match(identifier):
            raise UnmappableReferenceError(f"unsafe SQL identifier in reviewed edge: {identifier!r}")


def _deleted_pk_values(deleted_rows: Sequence, referenced_column: str, edge_id: str) -> tuple:
    """@description: Return the deleted PK values only when the referenced column is the sole PK column of the deleted table.
    @param deleted_rows: Deleted PK rows for the referenced table, one mapping per row.
    @param referenced_column: Column the reviewed edge points at.
    @return: Tuple of deleted PK values to pass as parameters.
    """
    if not deleted_rows:
        raise UnmappableReferenceError(f"deleted table has no PK rows for edge {edge_id}")
    column_sets = {tuple(sorted(row)) for row in deleted_rows}
    if len(column_sets) != 1 or column_sets.pop() != (referenced_column,):
        raise UnmappableReferenceError(f"referenced column is not the single-column PK for edge {edge_id}")
    return tuple(row[referenced_column] for row in deleted_rows)


def check_positive_control(base_table_count: object, fk_edge_count: object) -> bool:
    """@description: Confirm the read-only account can see the reviewed 23 base tables and 29 FK edges, so empty results are not read as absence.
    @param base_table_count: Visible base-table count from information_schema.
    @param fk_edge_count: Visible FK edge count from the reviewed metadata source.
    @return: True only when both counts equal the reviewed values exactly.
    """
    return _same_typed_value(base_table_count, EXPECTED_BASE_TABLE_COUNT) and _same_typed_value(fk_edge_count, EXPECTED_FK_EDGE_COUNT)


def find_failed_checks(
    facts: ReadonlyFacts,
    manifest: Mapping,
    options: ReadonlyVerificationOptions = ReadonlyVerificationOptions(),
) -> tuple:
    """@description: Name every verification check that failed so an operator sees the stop reason, not only the verdict.
    @param facts: Facts collected by read-only queries; None entries count as failures.
    @param manifest: Reviewed manifest used as the expected baseline.
    @param options: Gates for the unapproved REC-A9 and REC-A10 checks; both default to OFF.
    @return: Tuple of failed check names; empty when every applied check passed.
    """
    checks = _required_checks(facts, manifest)
    if options.positive_control_enabled:
        checks["visibility_insufficient"] = check_positive_control(facts.base_table_count, facts.fk_edge_count)
    if options.reference_residual_enabled:
        checks.update(_reference_residual_checks(facts, manifest))
    return tuple(name for name, passed in checks.items() if not passed)


def decide_readonly_verdict(
    facts: ReadonlyFacts,
    manifest: Mapping,
    options: ReadonlyVerificationOptions = ReadonlyVerificationOptions(),
) -> str:
    """@description: Produce the single Recovery verdict, so a partial pass can never be reported as verified.
    @param facts: Facts collected by read-only queries.
    @param manifest: Reviewed manifest used as the expected baseline.
    @param options: Gates for the unapproved REC-A9 and REC-A10 checks; both default to OFF.
    @return: VERDICT_VERIFIED only when no applied check failed; otherwise VERDICT_NOT_VERIFIED.
    """
    if find_failed_checks(facts, manifest, options):
        return VERDICT_NOT_VERIFIED
    return VERDICT_VERIFIED


def _required_checks(facts: ReadonlyFacts, manifest: Mapping) -> dict:
    """@description: Evaluate the always-required REC-A1 through REC-A7 checks against the reviewed baseline.
    @param facts: Facts collected by read-only queries.
    @param manifest: Reviewed manifest used as the expected baseline.
    @return: Mapping from stable check name to pass flag.
    """
    return {
        **_fk_rule_checks(facts, manifest),
        "target_mismatch": facts.target_check.is_authorized is True,
        "schema_fingerprint_mismatch": _fingerprints_match(facts),
        "fk_metadata_mismatch": _fk_edges_match(facts.fk_edges, manifest),
        "jb2_control_mismatch": _control_matches(facts.control_values),
        "schema_version_mismatch": _schema_version_matches(facts.applied_schema_version, manifest),
        "row_count_mismatch": _row_counts_match(facts.table_row_counts, facts.expected_row_counts),
        "deleted_pk_residual": _deleted_pk_residuals_zero(facts.deleted_pk_residual_counts, manifest),
        "statement_violation": _is_zero_count(facts.statement_violation_count),
    }


def _reference_residual_checks(facts: ReadonlyFacts, manifest: Mapping) -> dict:
    """@description: Evaluate REC-A9 only when enabled, reporting an unmappable manifest as a stop instead of an exception.
    @param facts: Facts collected by read-only queries.
    @param manifest: Reviewed manifest that defines the expected residual edges.
    @return: Mapping with one REC-A9 pass flag, or an unmappable-manifest failure.
    """
    try:
        expected_edge_ids = {query.edge_id for query in build_reference_residual_queries(manifest)}
    except UnmappableReferenceError:
        return {"reference_residual_unmappable": False}
    return {"reference_residual": _all_zero_for_keys(facts.reference_residual_counts, expected_edge_ids)}


def edge_identifier(edge: Sequence) -> str:
    """@description: Name one reviewed FK edge uniquely so residual counts and rule evidence key the same edge.
    @param edge: Six-field reviewed edge in manifest order.
    @return: Identifier of the form '<table>.<column>#<ordinal>-><referenced_table>.<referenced_column>'.
    """
    referencing_table, referencing_column, ordinal, _schema, referenced_table, referenced_column = _split_edge(edge)
    return f"{referencing_table}.{referencing_column}#{ordinal}->{referenced_table}.{referenced_column}"


def reviewed_edge_identifiers(manifest: Mapping) -> frozenset:
    """@description: List every reviewed FK edge identifier so rule evidence can be checked for complete coverage.
    @param manifest: Reviewed manifest holding reviewed_fk_column_edges.
    @return: Frozen set of identifiers for all reviewed edges.
    """
    return frozenset(edge_identifier(edge) for edge in manifest["reviewed_fk_column_edges"])


def _fk_rule_checks(facts: ReadonlyFacts, manifest: Mapping) -> dict:
    """@description: Fail closed on FK ON UPDATE/ON DELETE evidence, because REC-F4 needs rule values the manifest does not carry.
    @param facts: Facts carrying observed and approved per-edge rules, either of which may be None.
    @param manifest: Reviewed manifest supplying the expected edge identifiers.
    @return: Mapping with rule_source_missing and fk_rule_mismatch pass flags.
    """
    observed = facts.observed_fk_rules
    reviewed = facts.reviewed_fk_rules
    sources_present = isinstance(observed, Mapping) and isinstance(reviewed, Mapping)
    content_equal = sources_present and dict(observed) == dict(reviewed)
    provenance_valid = _provenance_is_valid(facts.reviewed_fk_provenance)
    return {
        "rule_source_missing": sources_present,
        "reviewed_rule_source_label_invalid": _reviewed_label_is_valid(facts.reviewed_fk_rules_source),
        "baseline_incomplete": not sources_present or _baseline_is_complete(reviewed, manifest),
        "baseline_provenance_invalid": provenance_valid,
        "baseline_unapproved": _provenance_is_approved(facts.reviewed_fk_provenance),
        "self_referential_rule_source": not sources_present or _is_independent_of_observed(
            observed, reviewed, content_equal, provenance_valid
        ),
        "fk_rule_mismatch": not sources_present or _rules_match(observed, reviewed, manifest),
    }


def _provenance_is_valid(provenance: object) -> bool:
    """@description: Require a complete provenance record with source id, approval reference, and an independence attestation.
    @param provenance: Caller-supplied baseline provenance record, which may be absent.
    @return: True only when every required field is present and the independence attestation is exactly True.
    """
    if not isinstance(provenance, Mapping):
        return False
    has_identifiers = all(
        isinstance(provenance.get(field_name), str) and provenance[field_name].strip() for field_name in REQUIRED_PROVENANCE_TEXT_FIELDS
    )
    return has_identifiers and provenance.get("independence_attested") is True


def _provenance_is_approved(provenance: object) -> bool:
    """@description: Accept the baseline only when its provenance record states the approved status.
    @param provenance: Caller-supplied baseline provenance record, which may be absent.
    @return: True only for a mapping whose approval_status is exactly 'approved'.
    """
    return isinstance(provenance, Mapping) and provenance.get("approval_status") == APPROVED_PROVENANCE_STATUS


def _baseline_is_complete(reviewed: Mapping, manifest: Mapping) -> bool:
    """@description: Require the baseline to cover exactly the 29 reviewed FK definitions, each with ON UPDATE and ON DELETE actions.
    @param reviewed: Caller-supplied baseline rules keyed by edge identifier.
    @param manifest: Reviewed manifest supplying the expected edge identifiers.
    @return: True only when the keys match exactly and every rule is a two-action pair of text.
    """
    if set(reviewed) != reviewed_edge_identifiers(manifest):
        return False
    return all(_is_well_formed_rule(rule) for rule in reviewed.values())


def _is_well_formed_rule(rule: object) -> bool:
    """@description: Check that one edge's rule holds exactly an ON UPDATE and an ON DELETE action as non-empty text.
    @param rule: Rule value for one edge.
    @return: True for a two-item list or tuple of non-empty strings.
    """
    if not isinstance(rule, (tuple, list)) or len(rule) != 2:
        return False
    return all(isinstance(action, str) and action.strip() for action in rule)


def _reviewed_label_is_valid(source_label: object) -> bool:
    """@description: Require the caller to name the reviewed source, and forbid the label that would mean the observed metadata.
    @param source_label: Caller-supplied name of the approved rule source.
    @return: True only for non-empty text that does not name the observed REFERENTIAL_CONSTRAINTS.
    """
    if not isinstance(source_label, str) or not source_label.strip():
        return False
    return source_label.strip().lower() != "observed"


def _is_independent_of_observed(
    observed: Mapping,
    reviewed: Mapping,
    content_equal: bool,
    provenance_valid: bool,
) -> bool:
    """@description: Refuse the observed object itself always, and equal content unless an independent provenance record vouches for it.
    @param observed: Per-edge rules read from REFERENTIAL_CONSTRAINTS.
    @param reviewed: Per-edge rules the caller presents as the approved baseline.
    @param content_equal: True when both rule sets hold the same content.
    @param provenance_valid: True when the baseline carries a complete independent provenance record.
    @return: True when the baseline is not the observed object and either differs from it or is independently attested.
    """
    if reviewed is observed:
        return False
    return provenance_valid or not content_equal


def _rules_match(observed: Mapping, reviewed: Mapping, manifest: Mapping) -> bool:
    """@description: Require both rule sets to cover exactly the 29 reviewed edges and agree on every edge.
    @param observed: Per-edge rules read from REFERENTIAL_CONSTRAINTS.
    @param reviewed: Per-edge rules approved by the Owner or DBA.
    @param manifest: Reviewed manifest supplying the expected edge identifiers.
    @return: True only for complete, identical coverage.
    """
    expected_identifiers = reviewed_edge_identifiers(manifest)
    if set(observed) != expected_identifiers or set(reviewed) != expected_identifiers:
        return False
    return dict(observed) == dict(reviewed)


def _is_zero_count(value: object) -> bool:
    """@description: Treat only a true integer zero as a clean count, so None or a boolean never counts as zero.
    @param value: Collected count.
    @return: True only for the integer 0.
    """
    return type(value) is int and value == 0


def _fingerprints_match(facts: ReadonlyFacts) -> bool:
    """@description: Require a present fingerprint that equals the baseline, so a failed collection never matches.
    @param facts: Facts collected by read-only queries.
    @return: True when both fingerprints are non-empty text and equal.
    """
    current = facts.schema_fingerprint
    return isinstance(current, str) and bool(current) and current == facts.baseline_fingerprint


def _fk_edges_match(actual_edges: object, manifest: Mapping) -> bool:
    """@description: Compare observed FK metadata with the reviewed edges as a multiset, so an empty or partial result never passes.
    @param actual_edges: Observed FK edges, each six fields in reviewed order.
    @param manifest: Reviewed manifest supplying the expected edges.
    @return: True only when the observed multiset equals the reviewed multiset.
    """
    try:
        observed = Counter(tuple(edge) for edge in actual_edges)
    except TypeError:
        return False
    reviewed = Counter(tuple(edge) for edge in manifest["reviewed_fk_column_edges"])
    return observed == reviewed


def _control_matches(control_values: object) -> bool:
    """@description: Require every REC-F5 jb2_control value to match the reviewed precondition exactly.
    @param control_values: Observed jb2_control singleton values.
    @return: True only when the mapping is present and every expected field matches.
    """
    if not isinstance(control_values, Mapping):
        return False
    return all(_same_typed_value(control_values.get(name), expected) for name, expected in EXPECTED_CONTROL_VALUES.items())


def _schema_version_matches(applied_version: object, manifest: Mapping) -> bool:
    """@description: Check the applied migration marker against the schema version the reviewed manifest records.
    @param applied_version: Applied schema marker read from jb2_migrations.
    @param manifest: Reviewed manifest carrying the expected schema_version.
    @return: True only when the observed marker is text equal to the reviewed version.
    """
    expected_version = manifest["control_restore"]["schema_version"]
    return isinstance(applied_version, str) and applied_version == expected_version


def _row_counts_match(actual_counts: object, expected_counts: object) -> bool:
    """@description: Require the observed per-table counts to cover exactly the expected tables with equal counts.
    @param actual_counts: Observed row counts keyed by table name.
    @param expected_counts: Baseline row counts keyed by table name.
    @return: True only when both mappings are non-empty, share the same keys, and agree on every count.
    """
    if not isinstance(actual_counts, Mapping) or not isinstance(expected_counts, Mapping) or not expected_counts:
        return False
    return dict(actual_counts) == dict(expected_counts)


def _deleted_pk_residuals_zero(residual_counts: object, manifest: Mapping) -> bool:
    """@description: Require every deleted-PK table to report a zero residual count, with no table missing from the result.
    @param residual_counts: Observed counts of surviving deleted PK rows, keyed by table.
    @param manifest: Reviewed manifest listing the ten deleted tables.
    @return: True only when the keys match the deleted tables and every count is zero.
    """
    if not isinstance(residual_counts, Mapping):
        return False
    if set(residual_counts) != set(manifest["delete_manifest"]):
        return False
    return all(_is_zero_count(count) for count in residual_counts.values())


def _all_zero_for_keys(observed_counts: object, expected_keys: set) -> bool:
    """@description: Require a zero count for exactly the expected edges, so a missing edge is never treated as clean.
    @param observed_counts: Observed residual counts keyed by edge identifier.
    @param expected_keys: Edge identifiers the reviewed manifest requires.
    @return: True only when the keys match exactly and every count is zero.
    """
    if not isinstance(observed_counts, Mapping) or set(observed_counts) != expected_keys:
        return False
    return all(_is_zero_count(count) for count in observed_counts.values())
