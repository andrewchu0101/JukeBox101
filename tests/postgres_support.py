"""Disposable PostgreSQL test databases for postgres-marked integration tests.

Connects only to a server whose cluster_name is the disposable-test marker, creates one uniquely named database per
test with the production role model (migrator / app / readonly), applies the shipped migrations as the migrator, and
drops only the database it created. Role passwords are random per session and never printed.
"""

import os
import secrets
from dataclasses import dataclass, replace
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from bot.config import DatabaseSettings
from bot.database.connection import open_connection
from bot.database.migration_runner import run_migrations

DISPOSABLE_CLUSTER_NAME = "jukebox101-disposable-test"  # [safety marker]: Set with -c cluster_name on the test server only.
DATABASE_PREFIX = "jb_t_"  # [ownership marker]: Only databases with this prefix created in this session are dropped.
APP_ROLE, MIGRATOR_ROLE, READONLY_ROLE = "jukebox101_app", "jukebox101_migrator", "jukebox101_readonly"
GROUP_ROLES = ("jukebox101_app_role", "jukebox101_readonly_role")


@dataclass(frozen=True)
class PostgresCluster:
    """Hold the admin connection target and per-session random role passwords for the disposable server."""

    host: str
    port: int
    admin_user: str
    admin_password: str
    role_passwords: dict  # [session credentials]: Random, in memory only.


@dataclass(frozen=True)
class PostgresDatabase:
    """Hold settings for each production role on one freshly migrated test database."""

    name: str
    app: DatabaseSettings
    migrator: DatabaseSettings
    readonly: DatabaseSettings
    admin: DatabaseSettings


def _environment_cluster() -> PostgresCluster | None:
    """@description: Read the disposable server location from JB_PG_TEST_* settings, or None to skip.
    @param: none
    @return: Cluster description without verifying it yet.
    """
    host = os.environ.get("JB_PG_TEST_HOST", "").strip()
    password_file = os.environ.get("JB_PG_TEST_ADMIN_PASSWORD_FILE", "").strip()
    password = Path(password_file).read_text(encoding="utf-8").strip() if password_file else os.environ.get("JB_PG_TEST_ADMIN_PASSWORD", "")
    if not host or not password:
        return None
    passwords = {role: secrets.token_urlsafe(24) for role in (APP_ROLE, MIGRATOR_ROLE, READONLY_ROLE)}
    return PostgresCluster(host, int(os.environ.get("JB_PG_TEST_PORT", "5432")), os.environ.get("JB_PG_TEST_ADMIN_USER", "postgres"),
                           password, passwords)


def admin_connect(cluster: PostgresCluster, database: str = "postgres") -> psycopg.Connection:
    """@description: Open an autocommit administrative connection used only for setup and teardown.
    @param cluster: Disposable server.
    @param database: Database to connect to.
    @return: Autocommit connection with dict rows.
    """
    return psycopg.connect(host=cluster.host, port=cluster.port, user=cluster.admin_user, password=cluster.admin_password,
                           dbname=database, sslmode="disable", autocommit=True, row_factory=dict_row, connect_timeout=5)


def prepare_cluster(cluster: PostgresCluster) -> None:
    """@description: Verify the disposable marker, then create or refresh the production role model with session passwords.
    @param cluster: Disposable server.
    @return: None; skips the tests when the marker is wrong so a real server is never touched.
    """
    with admin_connect(cluster) as connection:
        name = connection.execute("SELECT current_setting('cluster_name') AS name").fetchone()["name"]
        if name != DISPOSABLE_CLUSTER_NAME:
            pytest.exit(f"refusing: server cluster_name is not {DISPOSABLE_CLUSTER_NAME}", returncode=4)
        for group in GROUP_ROLES:
            _create_role_if_missing(connection, group, "NOLOGIN")
        _create_role_if_missing(connection, MIGRATOR_ROLE, "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE")
        _create_role_if_missing(connection, APP_ROLE, "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE")
        _create_role_if_missing(connection, READONLY_ROLE, "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE")
        connection.execute("GRANT jukebox101_app_role TO jukebox101_app")
        connection.execute("GRANT jukebox101_readonly_role TO jukebox101_readonly")
        for role, password in cluster.role_passwords.items():
            connection.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))


def _create_role_if_missing(connection: psycopg.Connection, role: str, options: str) -> None:
    """@description: Create a cluster role once; roles are shared by every test database on the disposable server.
    @param connection: Admin autocommit connection.
    @param role: Constant role name.
    @param options: Constant role options.
    @return: None.
    """
    if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None:
        connection.execute(sql.SQL("CREATE ROLE {} " + options).format(sql.Identifier(role)))


def _settings(cluster: PostgresCluster, database: str, user: str, password: str) -> DatabaseSettings:
    """@description: Build application-style settings for one role on the test database (TLS disabled on the isolated network).
    @param cluster: Disposable server.
    @param database: Test database name.
    @param user: Role name.
    @param password: Role password.
    @return: Database settings.
    """
    return DatabaseSettings(host=cluster.host, port=cluster.port, user=user, password=password, database=database, sslmode="disable")


def create_database(cluster: PostgresCluster, migrate: bool = True) -> PostgresDatabase:
    """@description: Create a fresh database exactly like docs/deploy/postgres-setup.sql and optionally apply all migrations.
    @param cluster: Prepared disposable server.
    @param migrate: True applies every shipped migration as the migrator role.
    @return: Settings for each role on the new database.
    """
    name = DATABASE_PREFIX + secrets.token_hex(6)
    with admin_connect(cluster) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
        connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO jukebox101_migrator, jukebox101_app, jukebox101_readonly")
                           .format(sql.Identifier(name)))
    with admin_connect(cluster, name) as connection:
        connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        connection.execute("CREATE SCHEMA jukebox101 AUTHORIZATION jukebox101_migrator")
        connection.execute("REVOKE ALL ON SCHEMA jukebox101 FROM PUBLIC")
    database = PostgresDatabase(name, *(_settings(cluster, name, role, cluster.role_passwords[role])
                                        for role in (APP_ROLE, MIGRATOR_ROLE, READONLY_ROLE)),
                                _settings(cluster, name, cluster.admin_user, cluster.admin_password))
    if migrate:
        apply_all_migrations(database)
    return database


def apply_all_migrations(database: PostgresDatabase) -> dict:
    """@description: Apply the shipped migrations through the production runner as the migrator role.
    @param database: Test database.
    @return: Runner summary.
    """
    connection = open_connection(database.migrator)
    try:
        return run_migrations(connection, apply=True)
    finally:
        connection.close()


def drop_database(cluster: PostgresCluster, name: str) -> None:
    """@description: Remove only a database this session created, after terminating its sessions.
    @param cluster: Disposable server.
    @param name: Database name, required to carry the session prefix.
    @return: None.
    """
    if not name.startswith(DATABASE_PREFIX):
        raise ValueError("refusing to drop a database this harness did not create")
    with admin_connect(cluster) as connection:
        connection.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


def admin_query(database: PostgresDatabase, statement: str, parameters: tuple = ()) -> list[dict]:
    """@description: Run one administrative statement on the test database (used for fault injection and assertions).
    @param database: Test database.
    @param statement: SQL text; constants only.
    @param parameters: Bound parameters.
    @return: Result rows, empty for statements without results.
    """
    with psycopg.connect(host=database.admin.host, port=database.admin.port, user=database.admin.user,
                         password=database.admin.password, dbname=database.name, sslmode="disable", autocommit=True,
                         row_factory=dict_row, options="-c search_path=jukebox101") as connection:
        cursor = connection.execute(statement, parameters)
        return cursor.fetchall() if cursor.description else []


def with_user(settings: DatabaseSettings, user: str, password: str) -> DatabaseSettings:
    """@description: Derive settings for another role on the same database.
    @param settings: Base settings.
    @param user: Role name.
    @param password: Role password.
    @return: New settings.
    """
    return replace(settings, user=user, password=password)
