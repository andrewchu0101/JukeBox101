"""Real PostgreSQL checks of the TLS settings, the readiness command and the migration command line.

TLS cases run only when the disposable server has ssl=on with its test-only CA (generated inside the test container
at setup); they prove verify-full with real libpq: right CA connects, wrong host name and missing CA are refused.
"""

import json
from dataclasses import replace

import psycopg
import pytest

from bot import migrate, readiness
from bot.database.connection import inspect_database_identity, open_connection
from tests.postgres_support import admin_query

pytestmark = pytest.mark.postgres
TEST_CA_PATH = "/var/lib/postgresql/jb-test-tls/ca.crt"  # [test-only CA]: Public certificate of the disposable server's CA.


@pytest.fixture
def ca_file(pg_database, tmp_path):
    """@description: Fetch the disposable server's public CA certificate, or skip when TLS is not enabled there.
    @param pg_database: Disposable database.
    @param tmp_path: Temporary directory.
    @return: Path of the CA certificate file.
    """
    if admin_query(pg_database, "SHOW ssl")[0]["ssl"] != "on":
        pytest.skip("disposable server has TLS disabled")
    pem = admin_query(pg_database, "SELECT pg_read_file(%s) AS pem", (TEST_CA_PATH,))[0]["pem"]
    path = tmp_path / "ca.crt"
    path.write_text(pem, encoding="ascii")
    return str(path)


def test_verify_full_with_the_right_ca_connects_over_tls(pg_database, ca_file):
    """@description: The production default mode verifies certificate and host name and the session is encrypted.
    @param pg_database: Disposable database.
    @param ca_file: Test CA.
    @return: None.
    """
    settings = replace(pg_database.app, sslmode="verify-full", sslrootcert=ca_file)
    connection = open_connection(settings)
    try:
        row = connection.execute("SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()
    finally:
        connection.close()
    assert row["ssl"] is True and row["version"].startswith("TLSv1.")
    assert inspect_database_identity(settings)["is_superuser"] is False


def test_verify_full_refuses_a_wrong_host_name_and_a_missing_ca(pg_database, ca_file):
    """@description: Connecting by IP (not in the certificate) or without any CA bundle fails before authentication.
    @param pg_database: Disposable database.
    @param ca_file: Test CA.
    @return: None.
    """
    server_address = admin_query(pg_database, "SELECT host(inet_server_addr()) AS address")[0]["address"]
    by_address = replace(pg_database.app, host=server_address, sslmode="verify-full", sslrootcert=ca_file)
    with pytest.raises(psycopg.OperationalError):
        open_connection(by_address).close()
    without_ca = replace(pg_database.app, sslmode="verify-full", sslrootcert="/nonexistent/ca.crt")
    with pytest.raises(psycopg.OperationalError):
        open_connection(without_ca).close()


def _role_environment(monkeypatch, settings) -> None:
    """@description: Expose one role's settings through the same DB_* variables the Bot and the CLIs read.
    @param monkeypatch: Environment isolation.
    @param settings: Role settings.
    @return: None.
    """
    for name, value in {"DB_HOST": settings.host, "DB_PORT": str(settings.port), "DB_USER": settings.user,
                        "DB_PASSWORD": settings.password, "DB_NAME": settings.database, "DB_SSLMODE": settings.sslmode}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("DB_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("DB_SSLROOTCERT", raising=False)


def test_readiness_reports_ready_then_names_each_problem(pg_database, monkeypatch, capsys):
    """@description: The read-only readiness command passes on a fresh database and names settlement or version problems.
    @param pg_database: Disposable database.
    @param monkeypatch: Environment isolation.
    @param capsys: Output capture.
    @return: None.
    """
    _role_environment(monkeypatch, pg_database.app)
    assert readiness.main() == 0
    assert json.loads(capsys.readouterr().out)["result"] == "ready"
    admin_query(pg_database, "UPDATE jb2_control SET settlement_enabled = TRUE, schema_version = 'old'")
    assert readiness.main() == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["problems"] == ["SCHEMA_VERSION_MISMATCH", "SETTLEMENT_ENABLED"]
    assert pg_database.app.password not in json.dumps(summary)


def test_migrate_cli_status_plan_and_guarded_apply(pg_database, monkeypatch, capsys):
    """@description: status/plan never write; apply without --apply is refused by argument parsing; the app role is refused.
    @param pg_database: Disposable database.
    @param monkeypatch: Environment isolation.
    @param capsys: Output capture.
    @return: None.
    """
    _role_environment(monkeypatch, pg_database.migrator)
    assert migrate.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["pending_before"] == []
    with pytest.raises(SystemExit):
        migrate.main(["apply"])
    capsys.readouterr()
    _role_environment(monkeypatch, pg_database.app)
    assert migrate.main(["plan"]) == migrate.EXIT_REFUSED
    assert "owned by the connecting role" in json.loads(capsys.readouterr().out)["reason"]
