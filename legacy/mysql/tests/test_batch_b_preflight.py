"""Test the user-run read-only preflight without connecting to MySQL or reading credentials.

Synthetic settings and mock cursors verify destination gates, sanitized reports and deterministic connection cleanup.
"""

from unittest.mock import MagicMock

import pymysql
import pytest

from bot.config import ConfigurationError
from tests import batch_b_preflight as preflight
from tests.mysql_safety import EXPECTED_TABLES
from bot.database.test_privileges import application_requirements


@pytest.fixture
def preflight_environment(monkeypatch) -> None:
    """@description: Supply only synthetic test credentials and explicit read authorization.
    @param monkeypatch: Pytest scoped environment injection.
    @return: None.
    """
    # [synthetic configuration]: No actual process secret is inspected by these tests.
    values = {"TEST_DB_HOST": "127.0.0.1", "TEST_DB_PORT": "3306", "TEST_DB_USER": "jukebox101_test_app",
              "TEST_DB_PASSWORD": "synthetic-only-password", "TEST_DB_NAME": "jukebox101_test",
              preflight.PREFLIGHT_SWITCH: "1"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_settings_never_fall_back_to_production(preflight_environment: None, monkeypatch) -> None:
    """@description: Require the dedicated password key even if a production password setting exists.
    @param preflight_environment: Explicit synthetic TEST_DB values.
    @param monkeypatch: Scoped environment substitution.
    @return: None.
    """
    monkeypatch.delenv("TEST_DB_PASSWORD")
    monkeypatch.setenv("DB_PASSWORD", "synthetic-production-trap")
    with pytest.raises(ConfigurationError, match="TEST_DB_PASSWORD"):
        preflight.preflight_settings()


@pytest.mark.parametrize("name,value", [("TEST_DB_HOST", "example.invalid"), ("TEST_DB_PORT", "3307"),
                                       ("TEST_DB_USER", "jukebox101_app"), ("TEST_DB_NAME", "jukebox101")])
def test_wrong_destination_fails_before_connection(preflight_environment: None, monkeypatch, name: str, value: str) -> None:
    """@description: Refuse any destination outside the existing fixed test boundary.
    @param preflight_environment: Synthetic valid destination.
    @param monkeypatch: Scoped setting injection.
    @param name: Destination field being corrupted.
    @param value: Nonapproved synthetic field value.
    @return: None.
    """
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError):
        preflight.preflight_settings()


def test_missing_opt_in_prevents_connection(monkeypatch) -> None:
    """@description: Ensure merely invoking the entry cannot connect without explicit read authorization.
    @param monkeypatch: Scoped switch removal.
    @return: None.
    """
    monkeypatch.delenv(preflight.PREFLIGHT_SWITCH, raising=False)
    with pytest.raises(ConfigurationError, match="NOT_AUTHORIZED"):
        preflight.preflight_settings()


def identity_row() -> dict:
    """@description: Provide a nonsecret MySQL 8 identity fixture.
    @param: none
    @return: Fixed authenticated test identity.
    """
    return {"database_name": "jukebox101_test", "account": "jukebox101_test_app@127.0.0.1",
            "mysql_version": "8.0.44", "server_port": 3306, "active_roles": "NONE"}


@pytest.mark.parametrize("field,value", [("database_name", "jukebox101"), ("account", "root@localhost"),
                                        ("server_port", 3307), ("mysql_version", "10.11-MariaDB"),
                                        ("active_roles", "`unreviewed_role`")])
def test_actual_identity_is_checked_before_inventory(field: str, value: object) -> None:
    """@description: Reject wrong server facts instead of trusting configured connection values.
    @param field: Authenticated server field being contradicted.
    @param value: Unapproved synthetic value.
    @return: None.
    """
    cursor = MagicMock()  # [identity query only]: No schema or application row is read after rejection.
    cursor.fetchone.return_value = dict(identity_row(), **{field: value})
    with pytest.raises(ConfigurationError):
        preflight._verify_identity(cursor)
    cursor.execute.assert_called_once()


def test_read_only_failure_always_rolls_back_and_closes(preflight_environment: None, monkeypatch) -> None:
    """@description: Keep preflight free of Commit even when identity verification fails.
    @param preflight_environment: Synthetic validated destination.
    @param monkeypatch: Fake connection injection only.
    @return: None.
    """
    connection = MagicMock()  # [offline connection]: No real driver call is possible.
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = dict(identity_row(), database_name="jukebox101")
    monkeypatch.setattr(preflight, "open_connection", MagicMock(return_value=connection))
    with pytest.raises(ConfigurationError):
        preflight.collect_preflight(preflight.preflight_settings())
    connection.begin.assert_called_once()
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
    connection.commit.assert_not_called()


def safe_privileges() -> dict:
    """@description: Model schema-scoped privileges without implying they were actually granted.
    @param: none
    @return: Minimal reported capability fixture for first-round review.
    """
    return {"global_privileges": [], "cross_schema_grants": False, "table_grants_present": False,
            "test_schema_privileges": [], "table_privileges": application_requirements()}


def test_empty_baseline_requires_review_and_never_authorizes_migration() -> None:
    """@description: Return a review artifact rather than silently proceeding to DDL after empty counts.
    @param: none
    @return: None.
    """
    cursor = MagicMock()  # [complete empty six-table fixture]: No application data is emitted.
    tables = [{"TABLE_NAME": name, "TABLE_TYPE": "BASE TABLE", "ENGINE": "InnoDB", "TABLE_COLLATION": "utf8mb4_bin"}
              for name in sorted(EXPECTED_TABLES)]
    cursor.fetchall.side_effect = [tables, [], []]
    cursor.fetchone.side_effect = [item for name in sorted(EXPECTED_TABLES)
                                  for item in ({"row_count": 0}, {"Create Table": "CREATE TABLE " + name})]
    report = preflight._inventory(cursor, identity_row(), safe_privileges())  # [review-only result]: No migration authorization.
    assert report["status"] == "READONLY_EMPTY_BASELINE_REVIEW_REQUIRED"
    assert report["migration_authorized"] is False and not report["migration"]
    assert all(call.args[0].startswith(("SELECT", "SHOW")) for call in cursor.execute.call_args_list)


@pytest.mark.parametrize("blocker", ["nonempty", "unknown", "view", "broad", "missing_dml", "existing_batch"])
def test_unsafe_assets_or_privileges_always_stop(blocker: str) -> None:
    """@description: Keep data and privilege uncertainty outside automatic migration eligibility.
    @param blocker: One known preflight stop condition.
    @return: None.
    """
    # [baseline facts]: Each variation models a single distinct review requirement.
    baseline = {name: {"row_count": 0} for name in EXPECTED_TABLES}
    tables = tuple({"TABLE_TYPE": "VIEW" if blocker == "view" else "BASE TABLE", "ENGINE": "InnoDB"} for _ in baseline)
    privileges = safe_privileges()
    if blocker == "nonempty":
        baseline["users"]["row_count"] = 1
    if blocker == "broad":
        privileges["global_privileges"] = ["SELECT"]
    if blocker == "missing_dml":
        privileges["test_schema_privileges"] = ["SELECT"]
        privileges["table_privileges"] = {}
    if blocker == "existing_batch":
        baseline["jb2_control"] = {"row_count": 0}
    assert preflight._stop_reasons(tables, ["unrecognized"] if blocker == "unknown" else [], baseline, privileges)


def test_view_is_not_counted_or_expanded_into_other_databases() -> None:
    """@description: Refuse to query a view named like an expected table because it might reference another database.
    @param: none
    @return: None.
    """
    cursor = MagicMock()  # [hostile view fixture]: Only information-schema metadata may be inspected.
    cursor.fetchall.side_effect = [[{"TABLE_NAME": "users", "TABLE_TYPE": "VIEW", "ENGINE": None}], [], []]
    report = preflight._inventory(cursor, identity_row(), safe_privileges())
    assert report["status"] == "STOP"
    assert not any("COUNT(*)" in call.args[0] for call in cursor.execute.call_args_list)


def test_arbitrary_sql_identifier_is_rejected_before_execute() -> None:
    """@description: Guard the only composed table identifiers with the static reviewed allowlist.
    @param: none
    @return: None.
    """
    cursor = MagicMock()  # [unexecuted cursor]: User-controlled identifiers cannot enter SQL.
    with pytest.raises(ConfigurationError):
        preflight._table_baseline(cursor, "users`; DROP DATABASE jukebox101; --")
    cursor.execute.assert_not_called()


def test_driver_error_message_is_never_printed(preflight_environment: None, monkeypatch, capsys) -> None:
    """@description: Expose a numeric diagnostic code without echoing raw authentication exceptions.
    @param preflight_environment: Synthetic environment only.
    @param monkeypatch: Offline failing connection injection.
    @param capsys: Captured user-facing diagnostics.
    @return: None.
    """
    monkeypatch.setattr(preflight, "open_connection", MagicMock(side_effect=pymysql.OperationalError(1045, "synthetic-secret-must-not-print")))
    with pytest.raises(SystemExit):
        preflight.main()
    output = capsys.readouterr().out  # [sanitized report]: No password or driver text is available to paste back.
    assert "1045" in output and "synthetic-secret" not in output
