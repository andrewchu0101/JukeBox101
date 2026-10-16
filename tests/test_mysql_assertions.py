"""Exercise foreign-key failure evidence with mocks and synthetic error messages.

Tests never connect to MySQL and verify that incorrect errors or accepted inserts still fail.
"""

from unittest.mock import MagicMock
from dataclasses import replace

import pymysql
import pytest

from tests import test_mysql
from tests.mysql_assertions import assert_missing_parent_error

GENERIC_FOREIGN_KEY_MESSAGE = "Cannot add or update a child row: a foreign key constraint fails"


@pytest.mark.parametrize("error_code", [1216, 1452])
@pytest.mark.parametrize("scenario", ["song", "owner"])
def test_real_test_functions_accept_only_expected_foreign_keys(error_code, scenario):
    """@description: Exercise both original integration functions using controlled mock parent evidence.
    @param error_code: Documented missing-parent error variant.
    @param scenario: Song-parent or compound-owner integration scenario.
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    constraint_name = "fk_recommendations_song" if scenario == "song" else "fk_dispatch_owner"
    message = GENERIC_FOREIGN_KEY_MESSAGE
    if error_code == 1452:
        message += f" (CONSTRAINT `{constraint_name}` FOREIGN KEY)"
    driver_error = pymysql.IntegrityError(error_code, message)
    if scenario == "song":
        cursor.fetchone.side_effect = [None, {"user_exists": 1, "song_exists": 0}]
        cursor.execute.side_effect = [None, None, driver_error]
        test_mysql.test_foreign_key_integrity((connection, 1, 2, "synthetic01"))
    else:
        cursor.fetchone.return_value = {"user_exists": 1, "song_exists": 1, "recommendation_exists": 1, "owner_exists": 0}
        cursor.execute.side_effect = [None, None, None, driver_error]
        test_mysql.test_dispatch_recommendation_owner((connection, 1, 2, "synthetic01"))


@pytest.mark.parametrize("driver_error", [
    pymysql.IntegrityError(1062, "Duplicate entry"),
    pymysql.IntegrityError(1217, GENERIC_FOREIGN_KEY_MESSAGE),
    pymysql.OperationalError(1452, GENERIC_FOREIGN_KEY_MESSAGE),
    pymysql.IntegrityError(1216, "Unrelated failure"),
    pymysql.IntegrityError(1452, GENERIC_FOREIGN_KEY_MESSAGE),
    pymysql.IntegrityError(1452, GENERIC_FOREIGN_KEY_MESSAGE + " (CONSTRAINT `fk_recommendations_user`)"),
    pymysql.IntegrityError(1216, GENERIC_FOREIGN_KEY_MESSAGE + " (CONSTRAINT `fk_recommendations_user`)"),
])
def test_reject_unrelated_or_ambiguous_detailed_errors(driver_error):
    """@description: Preserve strict error evidence rather than accepting arbitrary integrity failures.
    @param driver_error: Synthetic error with wrong type, code, message or constraint.
    @return: None.
    """
    with pytest.raises(AssertionError):
        assert_missing_parent_error(driver_error, "fk_recommendations_song")


@pytest.mark.parametrize("parent_evidence", [{"user_exists": 0, "song_exists": 0}, {"user_exists": 1, "song_exists": 1}])
def test_wrong_parent_conditions_fail_before_insert(parent_evidence):
    """@description: Prevent another missing parent or an existing song from masquerading as the intended failure.
    @param parent_evidence: Invalid mock parent existence evidence.
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [None, parent_evidence]
    with pytest.raises(AssertionError, match="Only the song"):
        test_mysql.test_foreign_key_integrity((connection, 1, 2, "synthetic01"))
    assert all(call.args[0].startswith("SELECT ") for call in cursor.execute.call_args_list)


def test_missing_foreign_key_rejection_still_fails():
    """@description: Ensure silently accepting an orphan row remains a failing integrity test.
    @param: none
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [None, {"user_exists": 1, "song_exists": 0}]
    with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
        test_mysql.test_foreign_key_integrity((connection, 1, 2, "synthetic01"))


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_failed_assertion_runs_fixture_cleanup(monkeypatch, database_settings, rollback_fails):
    """@description: Verify assertion failures still roll back and close the real fixture control path.
    @param monkeypatch: Mock and synthetic environment replacement facility.
    @param database_settings: Synthetic credentials from the unit-test fixture.
    @param rollback_fails: Whether the mock rollback itself raises a driver failure.
    @return: None.
    """
    monkeypatch.setenv("JUKEBOX_TEST_SCHEMA_READY", "1")
    monkeypatch.setenv("JUKEBOX_DISPOSABLE_DATABASE", "jukebox101_test")
    settings = replace(database_settings, user="jukebox101_test_app", database="jukebox101_test")
    connection = MagicMock()
    monkeypatch.setattr(test_mysql, "open_connection", MagicMock(return_value=connection))
    monkeypatch.setattr(test_mysql, "validate_connected_test_database", MagicMock())
    if rollback_fails:
        connection.rollback.side_effect = pymysql.OperationalError(2006, "Synthetic rollback failure")
    fixture_generator = test_mysql.isolated_mysql_rows.__wrapped__(settings)
    next(fixture_generator)
    with pytest.raises((AssertionError, pymysql.OperationalError)):
        fixture_generator.throw(AssertionError("Synthetic failed foreign-key assertion"))
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
    connection.commit.assert_not_called()
