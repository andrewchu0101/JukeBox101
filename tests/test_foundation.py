"""Verify configuration and transaction ownership without MySQL access.

Mocks exercise success and failure paths while synthetic secrets test safe diagnostics.
"""

from unittest.mock import MagicMock

import pymysql
import pytest

from bot.config import ConfigurationError, load_database_settings
from bot.database import connection as database_boundary


def test_environment_and_secret_representation(database_settings, caplog):
    """@description: Verify configuration works without Discord and keeps secrets private.
    @param database_settings: Synthetic validated settings.
    @param caplog: Captured diagnostic output.
    @return: None.
    """
    assert database_settings.database == "jukebox101"
    assert database_settings.password == "synthetic-secret"
    assert "synthetic-secret" not in repr(database_settings) + caplog.text


@pytest.mark.parametrize("setting_name", ["DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"])
def test_missing_required_settings(database_settings, monkeypatch, setting_name):
    """@description: Reject missing settings using only their public names.
    @param database_settings: Fixture initializing isolated configuration.
    @param monkeypatch: Environment isolation facility.
    @param setting_name: Required key removed for this case.
    @return: None.
    """
    monkeypatch.delenv(setting_name)
    with pytest.raises(ConfigurationError, match=setting_name):
        load_database_settings()


@pytest.mark.parametrize("port_value", ["secret-port", "0", "65536"])
def test_invalid_port(database_settings, monkeypatch, port_value):
    """@description: Validate ports without leaking malformed input.
    @param database_settings: Fixture initializing isolated configuration.
    @param monkeypatch: Environment isolation facility.
    @param port_value: Invalid TCP port input.
    @return: None.
    """
    monkeypatch.setenv("DB_PORT", port_value)
    with pytest.raises(ConfigurationError) as captured_error:
        load_database_settings()
    assert "secret-port" not in str(captured_error.value)
    assert captured_error.value.__cause__ is None


def test_dotenv_preserves_platform_environment(database_settings, monkeypatch, tmp_path):
    """@description: Load optional local settings without replacing platform values.
    @param database_settings: Isolated environment configuration.
    @param monkeypatch: Environment isolation facility.
    @param tmp_path: Disposable local file directory.
    @return: None.
    """
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("DB_HOST=other-host\nDB_PASSWORD=file-secret\n", encoding="utf-8")
    monkeypatch.delenv("DB_PASSWORD")
    loaded_settings = load_database_settings(dotenv_path)
    assert loaded_settings.host == database_settings.host
    assert loaded_settings.password == "file-secret"


def test_reject_root(database_settings, monkeypatch):
    """@description: Prevent accidental application use of administrative credentials.
    @param database_settings: Isolated environment configuration.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    monkeypatch.setenv("DB_USER", "root")
    with pytest.raises(ConfigurationError, match="non-administrative"):
        load_database_settings()


def test_connection_parameters(database_settings, monkeypatch):
    """@description: Verify driver options enforce the approved connection boundary.
    @param database_settings: Synthetic connection configuration.
    @param monkeypatch: Driver replacement facility.
    @return: None.
    """
    connect_mock = MagicMock()
    monkeypatch.setattr(database_boundary.pymysql, "connect", connect_mock)
    database_boundary.open_connection(database_settings)
    connection_options = connect_mock.call_args.kwargs
    assert connection_options["password"] == database_settings.password
    assert connection_options["host"] == "127.0.0.1"
    assert connection_options["port"] == 3306
    assert connection_options["user"] == "jukebox101_app"
    assert connection_options["database"] == "jukebox101"
    assert connection_options["cursorclass"] is database_boundary.DictCursor
    assert connection_options["autocommit"] is False
    assert connection_options["charset"] == "utf8mb4"


@pytest.mark.parametrize("failure_stage", [None, "begin", "body", "commit"])
def test_transaction_lifecycle(database_settings, monkeypatch, failure_stage):
    """@description: Commit success and close resources after all transaction failures.
    @param database_settings: Synthetic connection configuration.
    @param monkeypatch: Connection replacement facility.
    @param failure_stage: Transaction stage that raises a driver exception.
    @return: None.
    """
    owned_connection = MagicMock()
    database_failure = pymysql.OperationalError(2006, "Synthetic connection failure")
    transaction_body = MagicMock(side_effect=database_failure if failure_stage == "body" else None)
    monkeypatch.setattr(database_boundary, "open_connection", MagicMock(return_value=owned_connection))
    if failure_stage in ("begin", "commit"):
        getattr(owned_connection, failure_stage).side_effect = database_failure
    if failure_stage is None:
        with database_boundary.transaction(database_settings):
            pass
        owned_connection.commit.assert_called_once()
        owned_connection.rollback.assert_not_called()
    else:
        with pytest.raises(pymysql.OperationalError):
            with database_boundary.transaction(database_settings):
                transaction_body()
        owned_connection.rollback.assert_called_once()
    owned_connection.close.assert_called_once()


def test_parameterized_identity(database_settings, monkeypatch):
    """@description: Keep probe values separate from trusted SQL text.
    @param database_settings: Synthetic connection configuration.
    @param monkeypatch: Connection replacement facility.
    @return: None.
    """
    owned_connection = MagicMock()
    monkeypatch.setattr(database_boundary, "open_connection", MagicMock(return_value=owned_connection))
    database_boundary.inspect_database_identity(database_settings)
    execute_mock = owned_connection.cursor.return_value.__enter__.return_value.execute
    query_text, query_parameters = execute_mock.call_args.args
    assert "%s" in query_text
    assert query_parameters == ("jukebox101_connection_check",)
    assert query_parameters[0] not in query_text


def test_connect_failure_propagates(database_settings, monkeypatch, caplog):
    """@description: Preserve connection failures without logging credential contents.
    @param database_settings: Synthetic configuration.
    @param monkeypatch: Driver replacement facility.
    @param caplog: Captured diagnostic output.
    @return: None.
    """
    connect_mock = MagicMock(side_effect=pymysql.OperationalError(2003, "Unavailable"))
    monkeypatch.setattr(database_boundary.pymysql, "connect", connect_mock)
    with pytest.raises(pymysql.OperationalError):
        database_boundary.inspect_database_identity(database_settings)
    assert database_settings.password not in caplog.text


def test_query_failure_closes_connection(database_settings, monkeypatch):
    """@description: Release cursor and connection when parameterized queries fail.
    @param database_settings: Synthetic connection configuration.
    @param monkeypatch: Connection replacement facility.
    @return: None.
    """
    owned_connection = MagicMock()
    cursor_context = owned_connection.cursor.return_value
    cursor_context.__enter__.return_value.execute.side_effect = pymysql.ProgrammingError(1064, "Invalid query")
    monkeypatch.setattr(database_boundary, "open_connection", MagicMock(return_value=owned_connection))
    with pytest.raises(pymysql.ProgrammingError):
        database_boundary.inspect_database_identity(database_settings)
    cursor_context.__exit__.assert_called_once()
    owned_connection.rollback.assert_called_once()
    owned_connection.commit.assert_not_called()
    owned_connection.close.assert_called_once()


def test_rollback_failure_still_closes_connection(database_settings, monkeypatch):
    """@description: Close owned resources even when the database cannot roll back.
    @param database_settings: Synthetic connection configuration.
    @param monkeypatch: Connection replacement facility.
    @return: None.
    """
    owned_connection = MagicMock()
    owned_connection.rollback.side_effect = pymysql.OperationalError(2006, "Rollback unavailable")
    monkeypatch.setattr(database_boundary, "open_connection", MagicMock(return_value=owned_connection))
    with pytest.raises(pymysql.OperationalError) as captured_error:
        with database_boundary.transaction(database_settings):
            raise pymysql.ProgrammingError(1064, "Original query failure")
    assert isinstance(captured_error.value.__context__, pymysql.ProgrammingError)
    owned_connection.close.assert_called_once()
