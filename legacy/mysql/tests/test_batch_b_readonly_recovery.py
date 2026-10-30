"""Verify the offline Recovery read-only guards: target matching, statement allowlist, output redaction, and verdict.

Pure functions only; these tests open no sockets, import no database driver, and use synthetic facts plus the static manifest fixture.
"""

import ast
import dataclasses
import re
from pathlib import Path

import pytest

from tests import batch_b_readonly_recovery as readonly

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "docs" / "batch-b-recovery-manifest.json"
READONLY_ACCOUNT = "jukebox101_test_readonly"


def authorized_spec(**overrides: object) -> "readonly.TargetSpec":
    """@description: Build the authorized Recovery target with optional single-field overrides for mismatch tests.
    @param overrides: Field names mapped to replacement values.
    @return: Target specification identical to the authorized target unless overridden.
    """
    fields = {"host": "127.0.0.1", "port": 3306, "database": "jukebox101_test", "account": READONLY_ACCOUNT}
    fields.update(overrides)
    return readonly.TargetSpec(**fields)


# --- REC-F1 / REC-S4: target comparison ---


def test_authorized_target_is_accepted() -> None:
    """@description: Confirm the exact authorized target passes with no mismatched field reported."""
    result = readonly.check_target(authorized_spec())
    assert result.is_authorized is True
    assert result.mismatched_fields == ()


@pytest.mark.parametrize("host", ["localhost", "10.0.0.5", "127.0.0.2", ""])
def test_non_authorized_host_is_rejected(host: str) -> None:
    """@description: Confirm any host other than 127.0.0.1 is reported as a host mismatch."""
    result = readonly.check_target(authorized_spec(host=host))
    assert result.is_authorized is False
    assert "host" in result.mismatched_fields


@pytest.mark.parametrize("port", [3307, 0, "3306", None])
def test_non_authorized_port_is_rejected(port: object) -> None:
    """@description: Confirm a port other than the integer 3306 is rejected, including a string form of it."""
    result = readonly.check_target(authorized_spec(port=port))
    assert result.is_authorized is False
    assert "port" in result.mismatched_fields


@pytest.mark.parametrize("database", ["jukebox101", "jukebox101_app", "JUKEBOX101_TEST", ""])
def test_non_authorized_database_is_rejected(database: str) -> None:
    """@description: Confirm any database name other than jukebox101_test is rejected, including the production-like name."""
    result = readonly.check_target(authorized_spec(database=database))
    assert result.is_authorized is False
    assert "database" in result.mismatched_fields


@pytest.mark.parametrize(
    "account",
    ["jukebox101_test_app", "jukebox101_test_migrator", "jukebox101_test_harness", "JUKEBOX101_TEST_APP"],
)
def test_existing_application_accounts_cannot_be_readonly_account(account: str) -> None:
    """@description: Confirm the app, migrator, and harness accounts are never accepted as the Recovery read-only account."""
    result = readonly.check_target(authorized_spec(account=account))
    assert result.is_authorized is False
    assert "account" in result.mismatched_fields


def test_empty_account_is_rejected() -> None:
    """@description: Confirm a missing read-only account name is reported rather than silently accepted."""
    result = readonly.check_target(authorized_spec(account=""))
    assert result.is_authorized is False
    assert "account" in result.mismatched_fields


def test_every_mismatched_field_is_reported_together() -> None:
    """@description: Confirm multiple mismatches are all listed so the operator sees the full target problem at once."""
    result = readonly.check_target(authorized_spec(host="localhost", database="jukebox101"))
    assert set(result.mismatched_fields) == {"host", "database"}


# --- REC-S1 / REC-F2: single SELECT allowlist ---


@pytest.mark.parametrize(
    "sql_text",
    [
        "SELECT COUNT(*) AS residual_count FROM `songs` WHERE `song_id` IN (%s)",
        "select 1",
        "   SELECT table_name FROM information_schema.TABLES WHERE table_schema = %s   ",
    ],
)
def test_single_select_statement_is_allowed(sql_text: str) -> None:
    """@description: Confirm a single read-only SELECT with placeholders is accepted by the allowlist."""
    result = readonly.check_statement(sql_text)
    assert result.allowed is True


@pytest.mark.parametrize(
    "sql_text",
    [
        "INSERT INTO users (discord_user_id) VALUES (1)",
        "UPDATE users SET credit_balance = 0",
        "DELETE FROM songs",
        "DROP TABLE songs",
        "ALTER TABLE songs ADD COLUMN x INT",
        "CREATE TABLE t (id INT)",
        "GRANT SELECT ON *.* TO 'x'@'%'",
        "START TRANSACTION",
        "SET autocommit = 0",
        "SET FOREIGN_KEY_CHECKS = 0",
        "ROLLBACK",
        "COMMIT",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "CALL some_procedure()",
        "",
        "   ",
    ],
)
def test_non_select_statement_is_rejected(sql_text: str) -> None:
    """@description: Confirm every non-SELECT statement, including transaction control, is rejected without raising."""
    result = readonly.check_statement(sql_text)
    assert result.allowed is False
    assert result.reason


@pytest.mark.parametrize(
    "sql_text",
    [
        "SELECT 1; DROP TABLE songs",
        "SELECT 1;",
        "SELECT 1; SELECT 2",
    ],
)
def test_semicolon_multi_statement_text_is_rejected(sql_text: str) -> None:
    """@description: Confirm any semicolon in the text is rejected so a second statement can never ride along."""
    assert readonly.check_statement(sql_text).allowed is False


@pytest.mark.parametrize(
    "sql_text",
    [
        "SELECT 1 /*! ; DROP TABLE songs */",
        "SELECT 1 /* hidden */",
        "SELECT 1 -- trailing comment",
        "SELECT 1 # trailing comment",
    ],
)
def test_comment_markers_are_rejected(sql_text: str) -> None:
    """@description: Confirm comment markers are rejected because MySQL executable comments can hide statements."""
    assert readonly.check_statement(sql_text).allowed is False


@pytest.mark.parametrize(
    "sql_text",
    [
        "SELECT 1 INTO OUTFILE '/tmp/x'",
        "SELECT song_id FROM songs FOR UPDATE",
        "SELECT song_id FROM songs LOCK IN SHARE MODE",
    ],
)
def test_select_with_write_or_lock_clause_is_rejected(sql_text: str) -> None:
    """@description: Confirm SELECT forms that write files or take locks are rejected even though they start with SELECT."""
    assert readonly.check_statement(sql_text).allowed is False


@pytest.mark.parametrize("sql_input", [None, 1, b"SELECT 1"])
def test_non_text_statement_is_rejected_not_raised(sql_input: object) -> None:
    """@description: Confirm unknown input types are reported as rejections instead of raising from deep inside the guard."""
    result = readonly.check_statement(sql_input)  # type: ignore[arg-type]
    assert result.allowed is False


# --- module boundary: offline only ---


def test_module_imports_no_database_or_network_driver() -> None:
    """@description: Confirm the module source imports no database driver or socket layer, keeping the guard offline."""
    module_path = Path(readonly.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"pymysql", "socket", "ssl", "http", "urllib", "requests"}


# --- REC-F8: output redaction ---


@pytest.mark.parametrize(
    "secret_key",
    ["DB_PASSWORD", "DB_PASS", "api_token", "client_SECRET", "dsn_string", "Db_Pass_Hint"],
)
def test_secret_named_keys_are_dropped_with_their_values(secret_key: str) -> None:
    """@description: Confirm any key containing a secret marker is removed, and its value never reaches the summary."""
    summary = readonly.redact_summary({secret_key: "hunter2-value", "table_count": 23})
    assert secret_key not in summary
    assert "hunter2-value" not in repr(summary)
    assert summary["table_count"] == 23


def test_non_scalar_values_such_as_row_lists_are_dropped() -> None:
    """@description: Confirm lists and mappings that could carry full row values are never copied into the summary."""
    rows = [{"email": "private@example.invalid"}]
    summary = readonly.redact_summary({"sample_rows": rows, "nested": {"a": 1}, "row_count": 1})
    assert "sample_rows" not in summary
    assert "nested" not in summary
    assert "private@example.invalid" not in repr(summary)
    assert summary["row_count"] == 1


def test_free_text_row_value_is_dropped() -> None:
    """@description: Confirm a free-text string with spaces is not copied, since it may be a row value rather than an identifier."""
    summary = readonly.redact_summary({"last_seen": "Alice Chen logged in", "table_name": "jb2_versions"})
    assert "last_seen" not in summary
    assert summary["table_name"] == "jb2_versions"


def test_counts_booleans_and_fingerprints_are_kept() -> None:
    """@description: Confirm the allowed summary shapes (counts, booleans, sha256 fingerprints) pass through unchanged."""
    fingerprint = "a" * 64
    summary = readonly.redact_summary({"matches": True, "edge_count": 29, "schema_fingerprint": fingerprint})
    assert summary == {"matches": True, "edge_count": 29, "schema_fingerprint": fingerprint}


# --- REC-F3: schema fingerprint ---


def test_schema_fingerprint_ignores_auto_increment_counter() -> None:
    """@description: Confirm an advanced AUTO_INCREMENT counter does not change the structural fingerprint."""
    before = {"songs": "CREATE TABLE `songs` (...) ENGINE=InnoDB AUTO_INCREMENT=59 DEFAULT CHARSET=utf8mb4"}
    after = {"songs": "CREATE TABLE `songs` (...) ENGINE=InnoDB AUTO_INCREMENT=1200 DEFAULT CHARSET=utf8mb4"}
    assert readonly.compute_schema_fingerprint(before) == readonly.compute_schema_fingerprint(after)


def test_schema_fingerprint_changes_when_structure_changes() -> None:
    """@description: Confirm a changed column definition produces a different fingerprint, so schema drift is detected."""
    original = {"songs": "CREATE TABLE `songs` (`title` varchar(255)) ENGINE=InnoDB"}
    drifted = {"songs": "CREATE TABLE `songs` (`title` varchar(512)) ENGINE=InnoDB"}
    assert readonly.compute_schema_fingerprint(original) != readonly.compute_schema_fingerprint(drifted)


def test_empty_schema_definitions_are_not_fingerprinted() -> None:
    """@description: Confirm an empty metadata result raises instead of producing a fingerprint that could match the baseline."""
    with pytest.raises(ValueError):
        readonly.compute_schema_fingerprint({})


# --- manifest fixture and REC-A9 residual builder ---


def test_manifest_fixture_holds_29_reviewed_edges_and_15_delete_pks() -> None:
    """@description: Confirm the static manifest fixture carries the reviewed counts the verdict relies on."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    assert len(manifest["reviewed_fk_column_edges"]) == 29
    delete_pk_total = sum(len(rows) for rows in manifest["delete_manifest"].values())
    assert delete_pk_total == 15
    assert len(manifest["delete_manifest"]) == 10


def test_residual_builder_targets_exactly_the_23_edges_into_deleted_tables() -> None:
    """@description: Confirm the 6 edges into credit_ledger and recommendations are excluded from the residual check."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    queries = readonly.build_reference_residual_queries(manifest)
    assert len(queries) == 23
    assert all("credit_ledger.recommendation_id" not in query.edge_id for query in queries)
    assert not any("->recommendations." in query.edge_id or "->credit_ledger." in query.edge_id for query in queries)


def test_residual_queries_are_parameterized_counts_with_manifest_pk_values() -> None:
    """@description: Confirm each query is a COUNT with one %s per PK value and the values are passed as parameters only."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    pattern = re.compile(r"^SELECT COUNT\(\*\) AS residual_count FROM `\w+` WHERE `\w+` IN \(%s(, %s)*\)$")
    for query in readonly.build_reference_residual_queries(manifest):
        assert pattern.match(query.sql), query.sql
        assert query.sql.count("%s") == len(query.params) >= 1


def test_songs_residual_query_uses_deleted_song_id_as_parameter() -> None:
    """@description: Confirm the songs edge is counted against the deleted song_id 58 passed as a parameter, not interpolated."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    queries = {query.edge_id: query for query in readonly.build_reference_residual_queries(manifest)}
    songs_query = queries["credit_ledger.awarded_song_id#1->songs.song_id"]
    assert songs_query.sql == "SELECT COUNT(*) AS residual_count FROM `credit_ledger` WHERE `awarded_song_id` IN (%s)"
    assert songs_query.params == (58,)


def _manifest_with_edge(manifest: dict, index: int, edge: list) -> dict:
    """@description: Return a copy of the manifest with one reviewed edge replaced, so builder stop rules can be exercised.
    @param manifest: Loaded manifest fixture.
    @param index: Position of the edge to replace.
    @param edge: Replacement six-field edge.
    @return: Modified manifest copy; the fixture itself is not changed.
    """
    edited = dict(manifest)
    edited["reviewed_fk_column_edges"] = [list(item) for item in manifest["reviewed_fk_column_edges"]]
    edited["reviewed_fk_column_edges"][index] = edge
    return edited


@pytest.mark.parametrize(
    "replacement_edge",
    [
        ["credit_ledger", "awarded_song_id", 1, "jukebox101_test", "songs", "title"],
        ["credit_ledger", "awarded_song_id", 1, "jukebox101_test", "jb2_review_history", "video_id"],
        ["credit_ledger", "awarded_song_id", 1, "jukebox101_test", "users", "display_name"],
    ],
)
def test_non_primary_key_or_composite_reference_stops_the_builder(replacement_edge: list) -> None:
    """@description: Confirm an edge whose referenced column is not a single-column PK of a deleted table raises a named error."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    edited = _manifest_with_edge(manifest, 0, replacement_edge)
    with pytest.raises(readonly.UnmappableReferenceError):
        readonly.build_reference_residual_queries(edited)


@pytest.mark.parametrize(
    "replacement_edge",
    [
        ["credit_ledger; DROP TABLE songs", "awarded_song_id", 1, "jukebox101_test", "songs", "song_id"],
        ["credit_ledger", "awarded_song_id", 1, "other_schema", "songs", "song_id"],
    ],
)
def test_unsafe_identifier_or_foreign_schema_stops_the_builder(replacement_edge: list) -> None:
    """@description: Confirm identifiers that are not plain names, or edges from another schema, raise before any SQL is built."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    edited = _manifest_with_edge(manifest, 0, replacement_edge)
    with pytest.raises(readonly.UnmappableReferenceError):
        readonly.build_reference_residual_queries(edited)


# --- REC-A10: positive control, default OFF ---


def test_positive_control_is_not_applied_by_default() -> None:
    """@description: Confirm the unapproved positive control does not gate the default verdict, so it is never treated as approved."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(manifest, base_table_count=0, fk_edge_count=0)
    assert "visibility_insufficient" not in readonly.find_failed_checks(facts, manifest)


def test_enabled_positive_control_rejects_empty_visibility_as_insufficient() -> None:
    """@description: Confirm an empty base-table result is reported as visibility_insufficient and never read as absence."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(manifest, base_table_count=0)
    options = readonly.ReadonlyVerificationOptions(positive_control_enabled=True)
    assert readonly.decide_readonly_verdict(facts, manifest, options) == readonly.VERDICT_NOT_VERIFIED
    assert "visibility_insufficient" in readonly.find_failed_checks(facts, manifest, options)


@pytest.mark.parametrize(("base_table_count", "fk_edge_count"), [(23, 28), (22, 29), (None, 29), (23, None)])
def test_enabled_positive_control_rejects_any_count_other_than_23_and_29(base_table_count: object, fk_edge_count: object) -> None:
    """@description: Confirm the positive control fails on any base-table or FK-edge count other than the reviewed 23 and 29."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(manifest, base_table_count=base_table_count, fk_edge_count=fk_edge_count)
    options = readonly.ReadonlyVerificationOptions(positive_control_enabled=True)
    assert readonly.decide_readonly_verdict(facts, manifest, options) == readonly.VERDICT_NOT_VERIFIED


def test_enabled_positive_control_accepts_reviewed_counts() -> None:
    """@description: Confirm the exact reviewed counts 23 and 29 satisfy the enabled positive control, isolated from the rule gate."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(manifest)
    options = readonly.ReadonlyVerificationOptions(positive_control_enabled=True)
    assert "visibility_insufficient" not in readonly.find_failed_checks(facts, manifest, options)


# --- REC-A9: residual references, default OFF ---


def test_reference_residual_check_is_not_applied_by_default() -> None:
    """@description: Confirm non-zero residual reference counts are ignored unless the REC-A9 gate is explicitly enabled."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    residuals = {query.edge_id: 5 for query in readonly.build_reference_residual_queries(manifest)}
    facts = verified_facts(manifest, reference_residual_counts=residuals)
    assert "reference_residual" not in readonly.find_failed_checks(facts, manifest)


def test_enabled_reference_residual_rejects_any_nonzero_count() -> None:
    """@description: Confirm one surviving reference to a deleted PK makes the enabled REC-A9 check fail."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    residuals = reference_residual_zeroes(manifest)
    residuals["credit_ledger.awarded_song_id#1->songs.song_id"] = 1
    facts = verified_facts(manifest, reference_residual_counts=residuals)
    options = readonly.ReadonlyVerificationOptions(reference_residual_enabled=True)
    assert "reference_residual" in readonly.find_failed_checks(facts, manifest, options)


def test_enabled_reference_residual_rejects_missing_edge_result() -> None:
    """@description: Confirm an edge without a count is treated as not verified rather than as zero."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    residuals = reference_residual_zeroes(manifest)
    residuals.pop("credit_ledger.awarded_song_id#1->songs.song_id")
    facts = verified_facts(manifest, reference_residual_counts=residuals)
    options = readonly.ReadonlyVerificationOptions(reference_residual_enabled=True)
    assert readonly.decide_readonly_verdict(facts, manifest, options) == readonly.VERDICT_NOT_VERIFIED


def test_enabled_reference_residual_stops_on_unmappable_manifest() -> None:
    """@description: Confirm an unmappable manifest edge yields NOT_VERIFIED with a stable reason instead of an exception."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    edited = _manifest_with_edge(manifest, 0, ["credit_ledger", "awarded_song_id", 1, "jukebox101_test", "songs", "title"])
    facts = verified_facts(manifest, reference_residual_counts=reference_residual_zeroes(manifest))
    options = readonly.ReadonlyVerificationOptions(reference_residual_enabled=True)
    assert readonly.decide_readonly_verdict(facts, edited, options) == readonly.VERDICT_NOT_VERIFIED
    assert "reference_residual_unmappable" in readonly.find_failed_checks(facts, edited, options)


def reference_residual_zeroes(manifest: dict) -> dict:
    """@description: Build a zero-count REC-A9 result keyed by every reviewed edge into a deleted table.
    @param manifest: Loaded manifest fixture.
    @return: Mapping from edge identifier to count 0.
    """
    return {query.edge_id: 0 for query in readonly.build_reference_residual_queries(manifest)}


# --- REC-A1..A8 verdict ---


def verified_facts(manifest: dict, **overrides: object) -> "readonly.ReadonlyFacts":
    """@description: Build a set of collected facts that passes every non-rule check, with optional single-field overrides; the rule source stays self-referential.
    @param manifest: Loaded manifest fixture supplying the reviewed FK edges and deleted PKs.
    @param overrides: ReadonlyFacts field names mapped to replacement values.
    @return: Facts that satisfy every required check when no override is given.
    """
    baseline = readonly.compute_schema_fingerprint({"songs": "CREATE TABLE `songs` (...) ENGINE=InnoDB"})
    control = dict(manifest["control_restore"])
    facts = readonly.ReadonlyFacts(
        target_check=readonly.check_target(authorized_spec()),
        statement_violation_count=0,
        schema_fingerprint=baseline,
        baseline_fingerprint=baseline,
        fk_edges=tuple(tuple(edge) for edge in manifest["reviewed_fk_column_edges"]),
        control_values=control,
        applied_schema_version=control["schema_version"],
        table_row_counts={"songs": 3},
        expected_row_counts={"songs": 3},
        deleted_pk_residual_counts={table: 0 for table in manifest["delete_manifest"]},
        base_table_count=23,
        fk_edge_count=29,
        reference_residual_counts=reference_residual_zeroes(manifest),
        observed_fk_rules=synthetic_fk_rules(manifest),
        reviewed_fk_rules=synthetic_fk_rules(manifest),
        reviewed_fk_rules_source="synthetic-reviewed-fixture",
        reviewed_fk_provenance=None,
    )
    return dataclasses.replace(facts, **overrides)


def synthetic_fk_rules(manifest: dict) -> dict:
    """@description: Build a synthetic per-edge rule mapping for tests only; these are not reviewed production values.
    @param manifest: Loaded manifest fixture supplying the edge identifiers.
    @return: Mapping from every reviewed edge identifier to a fixed (ON UPDATE, ON DELETE) pair.
    """
    return {edge_id: ("RESTRICT", "RESTRICT") for edge_id in all_reviewed_edge_ids(manifest)}


def all_reviewed_edge_ids(manifest: dict) -> set:
    """@description: Name all 29 reviewed FK edges with the same identifier format used by the residual check.
    @param manifest: Loaded manifest fixture.
    @return: Set of edge identifiers for every reviewed edge.
    """
    return {
        f"{table}.{column}#{ordinal}->{ref_table}.{ref_column}"
        for table, column, ordinal, _schema, ref_table, ref_column in manifest["reviewed_fk_column_edges"]
    }


def test_consistent_facts_are_blocked_only_by_the_rule_source_checks() -> None:
    """@description: Confirm every non-rule check passes on consistent facts, and the only failures are rule-source checks, since the repo holds no independent approved baseline."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(manifest)
    failed = set(readonly.find_failed_checks(facts, manifest))
    assert "self_referential_rule_source" in failed
    assert failed <= RULE_CHECK_NAMES
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED


@pytest.mark.parametrize(
    ("overrides", "expected_failure"),
    [
        ({"target_check": readonly.check_target(authorized_spec(host="localhost"))}, "target_mismatch"),
        ({"statement_violation_count": 1}, "statement_violation"),
        ({"statement_violation_count": None}, "statement_violation"),
        ({"schema_fingerprint": "b" * 64}, "schema_fingerprint_mismatch"),
        ({"schema_fingerprint": None}, "schema_fingerprint_mismatch"),
        ({"fk_edges": ()}, "fk_metadata_mismatch"),
        ({"fk_edges": None}, "fk_metadata_mismatch"),
        ({"control_values": {**readonly.EXPECTED_CONTROL_VALUES, "snapshot_ready": 1}}, "jb2_control_mismatch"),
        ({"control_values": None}, "jb2_control_mismatch"),
        ({"applied_schema_version": "other"}, "schema_version_mismatch"),
        ({"table_row_counts": {"songs": 4}}, "row_count_mismatch"),
        ({"table_row_counts": {}}, "row_count_mismatch"),
        ({"deleted_pk_residual_counts": {"songs": 1}}, "deleted_pk_residual"),
        ({"deleted_pk_residual_counts": None}, "deleted_pk_residual"),
    ],
)
def test_each_required_check_failure_yields_not_verified(overrides: dict, expected_failure: str) -> None:
    """@description: Confirm each required REC-A1..A8 failure, including unknown or empty input, yields NOT_VERIFIED with a named reason."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(manifest, **overrides)
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert expected_failure in readonly.find_failed_checks(facts, manifest)


def test_partial_verdicts_are_never_returned() -> None:
    """@description: Confirm the verdict is always one of the two constants, never a partial or intermediate state."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    verdicts = {
        readonly.decide_readonly_verdict(verified_facts(manifest, fk_edges=None), manifest),
        readonly.decide_readonly_verdict(verified_facts(manifest), manifest),
    }
    assert verdicts == {readonly.VERDICT_NOT_VERIFIED}


# --- REC-F4: ON UPDATE / ON DELETE rule source, fail closed ---


@pytest.mark.parametrize(("observed", "reviewed"), [(None, "reviewed"), ("observed", None), (None, None)])
def test_missing_fk_rule_source_yields_not_verified(observed: object, reviewed: object) -> None:
    """@description: Confirm FK rules that cannot be read from REFERENTIAL_CONSTRAINTS or an approved baseline never verify, so the verdict fails closed."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    rules = synthetic_fk_rules(manifest)
    facts = verified_facts(
        manifest,
        observed_fk_rules=rules if observed == "observed" else None,
        reviewed_fk_rules=rules if reviewed == "reviewed" else None,
    )
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert "rule_source_missing" in readonly.find_failed_checks(facts, manifest)


def test_fk_rule_mismatch_on_one_edge_yields_not_verified() -> None:
    """@description: Confirm one observed ON DELETE rule that differs from the approved baseline blocks verification."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    observed = synthetic_fk_rules(manifest)
    observed["credit_ledger.awarded_song_id#1->songs.song_id"] = ("RESTRICT", "CASCADE")
    facts = verified_facts(manifest, observed_fk_rules=observed)
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert "fk_rule_mismatch" in readonly.find_failed_checks(facts, manifest)


def test_partial_fk_rule_coverage_yields_not_verified() -> None:
    """@description: Confirm rules covering fewer than all 29 reviewed edges do not verify, so missing edges are never read as clean."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    partial = synthetic_fk_rules(manifest)
    partial.pop("credit_ledger.awarded_song_id#1->songs.song_id")
    facts = verified_facts(manifest, observed_fk_rules=partial)
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED




# --- Owner decision: expected FK rules must come from a reviewed source, never from observed metadata ---


def test_same_object_as_observed_and_reviewed_rules_is_refused() -> None:
    """@description: Confirm passing the observed rule object again as the reviewed baseline is refused, since it cannot answer its own expectation."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    rules = synthetic_fk_rules(manifest)
    facts = verified_facts(manifest, observed_fk_rules=rules, reviewed_fk_rules=rules)
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert "self_referential_rule_source" in readonly.find_failed_checks(facts, manifest)


def test_equal_rule_set_as_observed_and_reviewed_is_refused() -> None:
    """@description: Confirm an equal-content reviewed set that merely copies the observed rules is refused as self-referential."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(
        manifest,
        observed_fk_rules=synthetic_fk_rules(manifest),
        reviewed_fk_rules=synthetic_fk_rules(manifest),
    )
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert "self_referential_rule_source" in readonly.find_failed_checks(facts, manifest)


@pytest.mark.parametrize("label", ["observed", "Observed", "  OBSERVED  ", "", None])
def test_reviewed_source_label_must_be_supplied_and_not_observed(label: object) -> None:
    """@description: Confirm a missing reviewed-source label, or one naming the observed metadata, refuses verification."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(manifest, reviewed_fk_rules_source=label)
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert "reviewed_rule_source_label_invalid" in readonly.find_failed_checks(facts, manifest)


# --- Owner ruling: equal content is a required match; independence comes only from a provenance record ---

RULE_CHECK_NAMES = {
    "rule_source_missing",
    "reviewed_rule_source_label_invalid",
    "baseline_incomplete",
    "baseline_provenance_invalid",
    "baseline_unapproved",
    "self_referential_rule_source",
    "fk_rule_mismatch",
}


def synthetic_provenance(**overrides: object) -> dict:
    """@description: Build a provenance record that only exercises the acceptance logic; its values describe a test fixture and are not a real approval.
    @param overrides: Provenance fields replaced for negative cases.
    @return: Provenance mapping with test-only identifiers.
    """
    record = {
        "source_id": "TEST-FIXTURE-NOT-A-REAL-BASELINE",
        "approval_status": "approved",
        "approval_reference": "TEST-FIXTURE-NO-REAL-APPROVAL",
        "independence_attested": True,
    }
    record.update(overrides)
    return record


def test_independent_approved_baseline_equal_to_observed_passes_rule_checks() -> None:
    """@description: Confirm an independently supplied, approved baseline whose content equals observed rules passes every rule check."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(
        manifest,
        observed_fk_rules=synthetic_fk_rules(manifest),
        reviewed_fk_rules=synthetic_fk_rules(manifest),
        reviewed_fk_provenance=synthetic_provenance(),
    )
    assert not set(readonly.find_failed_checks(facts, manifest)) & RULE_CHECK_NAMES


def test_observed_copy_without_provenance_is_refused_as_self_source() -> None:
    """@description: Confirm observed content copied into the baseline slot without independent approval evidence is refused as self-referential."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(manifest, observed_fk_rules=synthetic_fk_rules(manifest), reviewed_fk_rules=synthetic_fk_rules(manifest))
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert "self_referential_rule_source" in readonly.find_failed_checks(facts, manifest)


def test_same_object_is_refused_even_with_approved_provenance() -> None:
    """@description: Confirm the observed object itself is never accepted as baseline, even when a provenance record claims approval."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    rules = synthetic_fk_rules(manifest)
    facts = verified_facts(manifest, observed_fk_rules=rules, reviewed_fk_rules=rules, reviewed_fk_provenance=synthetic_provenance())
    assert "self_referential_rule_source" in readonly.find_failed_checks(facts, manifest)


@pytest.mark.parametrize(
    ("provenance", "expected_reason"),
    [
        (synthetic_provenance(approval_status="draft"), "baseline_unapproved"),
        (synthetic_provenance(approval_reference=""), "baseline_provenance_invalid"),
        (synthetic_provenance(source_id=""), "baseline_provenance_invalid"),
        (synthetic_provenance(independence_attested=False), "baseline_provenance_invalid"),
        (None, "baseline_provenance_invalid"),
    ],
)
def test_unapproved_or_unknown_provenance_is_refused_with_named_reason(provenance: object, expected_reason: str) -> None:
    """@description: Confirm an unapproved, unattested, or unidentified baseline is refused with its own reason code."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    facts = verified_facts(
        manifest,
        observed_fk_rules=synthetic_fk_rules(manifest),
        reviewed_fk_rules=synthetic_fk_rules(manifest),
        reviewed_fk_provenance=provenance,
    )
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert expected_reason in readonly.find_failed_checks(facts, manifest)


def test_incomplete_baseline_is_refused_as_incomplete() -> None:
    """@description: Confirm a baseline covering fewer than the 29 reviewed FK definitions is refused with baseline_incomplete."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    partial = synthetic_fk_rules(manifest)
    partial.pop("credit_ledger.awarded_song_id#1->songs.song_id")
    facts = verified_facts(manifest, reviewed_fk_rules=partial, reviewed_fk_provenance=synthetic_provenance())
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert "baseline_incomplete" in readonly.find_failed_checks(facts, manifest)


def test_mismatching_baseline_is_refused_as_rule_mismatch() -> None:
    """@description: Confirm a complete, approved baseline that disagrees with observed rules on one edge is refused as fk_rule_mismatch."""
    manifest = readonly.load_recovery_manifest(MANIFEST_PATH)
    reviewed = synthetic_fk_rules(manifest)
    reviewed["credit_ledger.awarded_song_id#1->songs.song_id"] = ("RESTRICT", "CASCADE")
    facts = verified_facts(manifest, reviewed_fk_rules=reviewed, reviewed_fk_provenance=synthetic_provenance())
    assert readonly.decide_readonly_verdict(facts, manifest) == readonly.VERDICT_NOT_VERIFIED
    assert "fk_rule_mismatch" in readonly.find_failed_checks(facts, manifest)
