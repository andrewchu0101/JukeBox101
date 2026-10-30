"""Exercise separate test identities and grants without opening database connections.

Synthetic settings and metadata verify closed switches, grant coverage and sanitized failure boundaries.
"""

from dataclasses import replace
from unittest.mock import MagicMock
import pytest
from bot.config import ConfigurationError, DatabaseSettings
from bot.database.test_profiles import profile_settings, validate_profile, MIGRATION_SWITCH
from bot.database.test_privileges import collect_privileges, migration_requirements, privilege_blockers, ORIGINAL_TABLES, application_requirements
from bot.database.submission_migration import apply_migration, MIGRATION_AUTHORIZATION, migration_plan
from tests import batch_b_preflight, batch_b_migration_preflight, batch_b_migrate

SETTINGS = DatabaseSettings("127.0.0.1", 3306, "jukebox101_test_migrator", "synthetic-secret", "jukebox101_test")


@pytest.mark.parametrize("field,value", [("host", "localhost"), ("port", 3307), ("database", "jukebox101"),
                                       ("user", "jukebox101_test_app"), ("password", "")])
def test_migration_profile_rejected_before_factory(monkeypatch, field: str, value: object) -> None:
    """@description: Prevent settings mismatches from reaching the supplied connection factory.
    @param monkeypatch: Synthetic authorization switch.
    @param field: Incorrect destination field.
    @param value: Forbidden synthetic value.
    @return: None.
    """
    monkeypatch.setenv(MIGRATION_SWITCH, "1")
    factory = MagicMock()  # [unused factory]: Rejection must precede connection.
    with pytest.raises(ConfigurationError):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=replace(SETTINGS, **{field: value}))
    factory.assert_not_called()


def test_execution_default_closed(monkeypatch) -> None:
    """@description: Keep the runner closed even with a valid dedicated profile.
    @param monkeypatch: Remove explicit execution consent.
    @return: None.
    """
    monkeypatch.delenv(MIGRATION_SWITCH, raising=False)
    factory = MagicMock()  # [unused factory]: No credentials are transmitted.
    with pytest.raises(ConfigurationError, match="NOT_AUTHORIZED"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=SETTINGS)
    factory.assert_not_called()


def test_migrator_cannot_be_application() -> None:
    """@description: Prevent DDL credentials from entering the application preflight.
    @param: none
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        validate_profile(SETTINGS)


@pytest.mark.parametrize("missing", ["HOST", "PORT", "USER", "PASSWORD", "NAME"])
def test_profiles_have_no_fallback(monkeypatch, missing: str) -> None:
    """@description: Reject absent migration keys even when application and production keys exist.
    @param monkeypatch: Synthetic environment injection.
    @param missing: Required dedicated key to omit.
    @return: None.
    """
    monkeypatch.setenv("READ_OPT_IN", "1")
    for suffix, value in {"HOST": "127.0.0.1", "PORT": "3306", "USER": SETTINGS.user,
                          "PASSWORD": "synthetic-secret", "NAME": "jukebox101_test"}.items():
        monkeypatch.setenv("TEST_MIGRATION_DB_" + suffix, value)
        monkeypatch.setenv("TEST_DB_" + suffix, value)
        monkeypatch.setenv("DB_" + suffix, value)
    monkeypatch.delenv("TEST_MIGRATION_DB_" + missing)
    with pytest.raises(ConfigurationError):
        profile_settings("READ_OPT_IN", True)


def grants_fixture() -> dict:
    """@description: Provide table-only migration grants including future table CREATE rights.
    @param: none
    @return: Nonsecret minimum grant metadata.
    """
    return {"test_schema_privileges": [], "table_privileges": migration_requirements(),
            "global_privileges": [], "cross_schema_grants": False, "grant_option": False}


def test_table_grants_cover_migration_and_parent_foreign_keys() -> None:
    """@description: Ensure grant requirements follow the actual reviewed FK parents and table plan.
    @param: none
    @return: None.
    """
    grants = grants_fixture()  # [minimum capabilities]: No schema-wide DDL is necessary.
    assert not privilege_blockers(grants, set(ORIGINAL_TABLES), True)
    assert {statement.split()[5] for statement in migration_plan()[1]}.issubset(grants["table_privileges"])
    for parent in ("users", "songs", "credit_ledger", "jb2_versions", "jb2_events", "jb2_videos"):
        assert "REFERENCES" in grants["table_privileges"][parent]
    grants["table_privileges"]["songs"].remove("REFERENCES")
    assert "MIGRATION_PRIVILEGES_MISSING" in privilege_blockers(grants, set(ORIGINAL_TABLES), True)


def test_application_table_dml_without_ddl_is_ready() -> None:
    """@description: Accept table-level application DML while refusing application DDL.
    @param: none
    @return: None.
    """
    grants = grants_fixture()  # [application capabilities]: Fully separate from the migration grant fixture.
    grants["table_privileges"] = application_requirements()
    assert not privilege_blockers(grants, set(ORIGINAL_TABLES), False)
    grants["table_privileges"]["songs"].add("CREATE")
    assert "EXCESS_ACCOUNT_PRIVILEGES" in privilege_blockers(grants, set(ORIGINAL_TABLES), False)


def test_privilege_metadata_combines_schema_and_table_grants() -> None:
    """@description: Query the migrator grantee and retain individual table rights without secret text.
    @param: none
    @return: None.
    """
    cursor = MagicMock()  # [offline metadata]: No SHOW GRANTS or driver connection.
    cursor.fetchall.side_effect = [[], [{"TABLE_SCHEMA": "jukebox101_test", "PRIVILEGE_TYPE": "SELECT"}],
                                  [{"TABLE_SCHEMA": "jukebox101_test", "TABLE_NAME": "songs", "PRIVILEGE_TYPE": "REFERENCES"}]]
    grants = collect_privileges(cursor, True)
    assert grants["table_privileges"]["songs"] == ["REFERENCES"]
    assert all(call.args[1] == ("'jukebox101_test_migrator'@'127.0.0.1'",) for call in cursor.execute.call_args_list)


def test_migration_preflight_is_independent_and_sanitized(monkeypatch, capsys) -> None:
    """@description: Block missing migrator authorization without attempting inventory or leaking passwords.
    @param monkeypatch: Remove migration read opt-in.
    @param capsys: Sanitized CLI output capture.
    @return: None.
    """
    monkeypatch.delenv(batch_b_migration_preflight.PREFLIGHT_SWITCH, raising=False)
    collector = MagicMock()
    monkeypatch.setattr(batch_b_preflight, "collect_preflight", collector)
    with pytest.raises(SystemExit):
        batch_b_migration_preflight.main()
    collector.assert_not_called()
    assert "synthetic-secret" not in capsys.readouterr().out


def test_migration_cli_stops_before_ddl_on_inventory_blocker(monkeypatch) -> None:
    """@description: Never turn an unsafe read-only report into migration execution.
    @param monkeypatch: Supply only mock inventory and settings.
    @return: None.
    """
    runner = MagicMock()  # [unexecuted DDL]: Nonempty assets require manual review.
    monkeypatch.setattr(batch_b_migrate, "profile_settings", lambda *arguments: SETTINGS)
    monkeypatch.setattr(batch_b_migrate, "collect_preflight", lambda *arguments: {"stop_reasons": ["NONEMPTY_ASSETS_REQUIRE_REVIEW"]})
    monkeypatch.setattr(batch_b_migrate, "apply_migration", runner)
    assert batch_b_migrate.migrate()["stop_reasons"]
    runner.assert_not_called()


def test_migrator_excess_original_write_and_grant_option_are_rejected() -> None:
    """@description: Do not let minimum-right coverage hide extra authority over protected original rows.
    @param: none
    @return: None.
    """
    grants = grants_fixture()  # [forbidden extra capability]: Migrator must not modify old balances.
    grants["table_privileges"]["users"].add("UPDATE")
    assert "EXCESS_MIGRATION_PRIVILEGES" in privilege_blockers(grants, set(ORIGINAL_TABLES), True)
    grants = grants_fixture()
    grants["grant_option"] = True
    assert "EXCESS_ACCOUNT_PRIVILEGES" in privilege_blockers(grants, set(ORIGINAL_TABLES), True)


@pytest.mark.parametrize("table", ["jb2_migrations", "credit_ledger", "jb2_receipts", "jb2_claims", "jb2_review_history"])
@pytest.mark.parametrize("right", ["UPDATE", "DELETE"])
def test_application_immutable_tables_reject_extra_rights(table: str, right: str) -> None:
    """@description: Prevent accidental mutable grants on version records and append-only accounting facts.
    @param table: Reviewed immutable table.
    @param right: Unauthorized mutation capability.
    @return: None.
    """
    grants = grants_fixture()  # [application profile]: Use the actual per-table contract.
    grants["table_privileges"] = application_requirements()
    grants["table_privileges"][table].add(right)
    assert "EXCESS_APPLICATION_PRIVILEGES" in privilege_blockers(grants, set(application_requirements()), False)


def test_application_registry_insert_is_forbidden() -> None:
    """@description: Reserve all migration version writes for the dedicated runner account.
    @param: none
    @return: None.
    """
    grants = grants_fixture()  # [registry read-only boundary]: Even INSERT is excessive.
    grants["table_privileges"] = application_requirements()
    grants["table_privileges"]["jb2_migrations"].add("INSERT")
    assert "EXCESS_APPLICATION_PRIVILEGES" in privilege_blockers(grants, set(), False)


def test_application_schema_writes_cannot_be_overridden_by_read_only_table_grant() -> None:
    """@description: Catch additive schema DML even when registry table grants appear read-only.
    @param: none
    @return: None.
    """
    grants = grants_fixture()  # [legacy broad grant]: Narrow table grants cannot revoke inherited rights.
    grants["table_privileges"] = application_requirements()
    grants["test_schema_privileges"] = ["SELECT", "INSERT", "UPDATE", "DELETE"]
    assert "EXCESS_APPLICATION_PRIVILEGES" in privilege_blockers(grants, set(), False)


def test_schema_select_plus_table_mutations_satisfy_both_profiles() -> None:
    """@description: Preserve legitimate mixed-level grants without requiring schema writes.
    @param: none
    @return: None.
    """
    for migration, requirements in ((False, application_requirements()), (True, migration_requirements())):
        grants = grants_fixture()  # [mixed grant representation]: Effective rights are combined by table.
        grants["test_schema_privileges"] = ["SELECT"]
        grants["table_privileges"] = {name: rights - {"SELECT"} for name, rights in requirements.items()}
        assert not privilege_blockers(grants, set(requirements), migration)


def test_minimum_grant_artifact_matches_both_contracts() -> None:
    """@description: Keep the reviewed SQL draft synchronized with Python capability checks.
    @param: none
    @return: None.
    """
    import re
    from pathlib import Path
    source = Path("docs/batch-b-minimum-grants.sql").read_text(encoding="utf-8")
    for user, required in (("app", application_requirements()), ("migrator", migration_requirements())):
        pattern = r"^GRANT (.+) ON `jukebox101_test`\.`([^`]+)` TO 'jukebox101_test_" + user + r"'@'127\.0\.0\.1';$"
        actual = {table: set(rights.split(", ")) for rights, table in re.findall(pattern, source, re.MULTILINE)}
        assert actual == required
    assert application_requirements()["jb2_migrations"] == {"SELECT"}
    assert all("DELETE" not in rights for rights in application_requirements().values())


def test_migration_references_match_every_actual_fk_parent() -> None:
    """@description: Catch future migration FK additions that need a matching parent grant.
    @param: none
    @return: None.
    """
    import re
    parents = set(re.findall(r"REFERENCES ([a-z0-9_]+)\(", "\n".join(migration_plan()[1])))
    required = migration_requirements()  # [actual FK scope]: No REFERENCES needed on unrelated children.
    assert parents == {name for name, rights in required.items() if "REFERENCES" in rights}


def test_repository_sql_mutations_fit_application_grants() -> None:
    """@description: Detect an SQL write added without a corresponding reviewed per-table capability.
    @param: none
    @return: None.
    """
    import ast
    import re
    from pathlib import Path
    requirements = application_requirements()  # [runtime authority]: Migration and test cleanup are excluded.
    modules = ("repository", "submission_store", "submission_transaction", "reward_settlement",
               "review_persistence", "submission_maintenance", "reward_history", "persisted_snapshot")
    for name in modules:
        tree = ast.parse(Path("bot/database", name + ".py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                match = re.match(r"(INSERT INTO|UPDATE|DELETE FROM) ([a-z0-9_]+)\b", node.value)
                if match:
                    operation, table = match.groups()
                    assert operation.split()[0] in requirements[table], (name, table, operation)
                    if "ON DUPLICATE KEY UPDATE" in node.value:
                        assert "UPDATE" in requirements[table], (name, table)
