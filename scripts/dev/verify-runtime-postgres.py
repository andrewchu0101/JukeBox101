"""Feed to the runtime image's Python stdin on the disposable test network.

Uses only JB_PG_TEST_* credentials. Verifies the server marker BEFORE any write,
creates unique database/login roles, exercises real CLI subprocesses over TLS,
and removes only those resources in finally. Never starts the Bot.
"""
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql


def main():
    host = os.environ["JB_PG_TEST_HOST"]
    port = os.environ.get("JB_PG_TEST_PORT", "5432")
    admin = psycopg.connect(host=host, port=port,
        user=os.environ.get("JB_PG_TEST_ADMIN_USER", "postgres"),
        password=os.environ["JB_PG_TEST_ADMIN_PASSWORD"], dbname="postgres", sslmode="disable", autocommit=True)
    with admin:
        if admin.execute("SHOW cluster_name").fetchone()[0] != "jukebox101-disposable-test":
            raise RuntimeError("refusing server without disposable marker")
        suffix = secrets.token_hex(6)
        database, migrator, app = ["jb_t_" + label + suffix for label in ("cli", "mig", "app")]
        passwords = {role: secrets.token_urlsafe(32) for role in (migrator, app)}
        try:
            for role in (migrator, app):
                admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(passwords[role])))
            admin.execute(sql.SQL("GRANT jukebox101_app_role TO {}").format(sql.Identifier(app)))
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(database), sql.Identifier(migrator)))
            with psycopg.connect(host=host, port=port, user=os.environ.get("JB_PG_TEST_ADMIN_USER", "postgres"),
                    password=os.environ["JB_PG_TEST_ADMIN_PASSWORD"], dbname=database, sslmode="disable", autocommit=True) as setup:
                setup.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
                setup.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                    sql.Identifier(database), sql.Identifier(migrator), sql.Identifier(app)))
                setup.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                setup.execute(sql.SQL("CREATE SCHEMA jukebox101 AUTHORIZATION {}").format(sql.Identifier(migrator)))
            ca = admin.execute("SELECT pg_read_file('/var/lib/postgresql/jb-test-tls/ca.crt')").fetchone()[0]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "ca.crt"
                path.write_text(ca)
                env = {k: v for k, v in os.environ.items() if not k.startswith(("DB_", "JB_PG_TEST_"))}
                env.update(DB_HOST=host, DB_PORT=os.environ.get("JB_PG_TEST_PORT", "5432"), DB_NAME=database,
                    DB_SSLMODE="verify-full", DB_SSLROOTCERT=str(path))
                for module, arguments, role in [
                    ("bot.migrate", ["status"], migrator), ("bot.migrate", ["plan"], migrator),
                    ("bot.migrate", ["apply", "--apply"], migrator), ("bot.migrate", ["status"], migrator),
                    ("bot.readiness", [], app),
                ]:
                    env.update(DB_USER=role, DB_PASSWORD=passwords[role])
                    result = subprocess.run(["python", "-m", module, *arguments], env=env, text=True, capture_output=True)
                    if result.returncode:
                        raise RuntimeError(f"{module} failed ({result.returncode})")
                    output = json.loads(result.stdout)
                    if module == "bot.readiness" and (output["result"] != "ready" or output["tls_mode"] != "verify-full"):
                        raise RuntimeError("readiness not ready over verify-full")
                    print(json.dumps({"module": module, "arguments": arguments, "output": output}), flush=True)
        finally:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
            for role in (app, migrator):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
    print("RUNTIME CLI VERIFIED; disposable resources removed", flush=True)


if __name__ == "__main__":
    main()
