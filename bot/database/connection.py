"""Own synchronous PostgreSQL connections and short transactional work units.

Validated settings create fresh psycopg connections (dict rows, JSON kept as text, UTC session) closed in their worker.
"""

from contextlib import contextmanager
from collections.abc import Iterator

import psycopg
from psycopg.adapt import AdaptersMap
from psycopg.rows import dict_row
from psycopg.types.string import TextLoader

from bot.config import DatabaseSettings

CONNECTION_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MILLISECONDS = 10_000  # [query bound]: Mirrors the former 10 s read/write timeout per statement.
LOCK_TIMEOUT_MILLISECONDS = 5_000  # [lock bound]: Upper bound for any row lock; intake sets a shorter local bound.
APPLICATION_NAME = "jukebox101-bot"  # [server-side label]: Visible in pg_stat_activity; contains no secret.
APPLICATION_SCHEMA = "jukebox101"  # [fixed schema]: Every table lives here; migrations and grants name it explicitly.
# [session settings]: UTC session clock and bounded waits; sent as connection options so every transaction inherits them.
SESSION_OPTIONS = (f"-c search_path={APPLICATION_SCHEMA} -c TimeZone=UTC -c statement_timeout={STATEMENT_TIMEOUT_MILLISECONDS} "
                   f"-c lock_timeout={LOCK_TIMEOUT_MILLISECONDS} -c idle_in_transaction_session_timeout=60000")
# [JSON as text]: json/jsonb columns load as their stored text so the existing contract codecs stay the only decoders.
JSON_AS_TEXT_ADAPTERS = AdaptersMap(psycopg.adapters)
for _json_type_name in ("json", "jsonb"):
    JSON_AS_TEXT_ADAPTERS.register_loader(_json_type_name, TextLoader)


def open_connection(settings: DatabaseSettings) -> psycopg.Connection:
    """@description: Give each worker an independent connection with explicit transaction control and verified TLS.
    @param settings: Validated application database credentials, destination and TLS policy.
    @return: New connection owned exclusively by its calling worker; the first statement opens a transaction.
    """
    tls_options = {"sslmode": settings.sslmode}
    if settings.sslrootcert is not None:
        tls_options["sslrootcert"] = settings.sslrootcert
    return psycopg.connect(
        host=settings.host, port=settings.port, user=settings.user, password=settings.password,
        dbname=settings.database, connect_timeout=CONNECTION_TIMEOUT_SECONDS, application_name=APPLICATION_NAME,
        options=SESSION_OPTIONS, autocommit=False, row_factory=dict_row, context=JSON_AS_TEXT_ADAPTERS, **tls_options,
    )


@contextmanager
def transaction(settings: DatabaseSettings) -> Iterator[psycopg.Connection]:
    """@description: Commit successful work and roll back failures before releasing resources.
    @param settings: Configuration used inside the calling worker thread.
    @return: Context yielding an exclusively owned transaction connection.
    """
    # [transaction ownership]: Connection creation, use and close stay in one worker.
    connection = open_connection(settings)
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def inspect_database_identity(settings: DatabaseSettings) -> dict:
    """@description: Verify the application destination and privilege level without requiring schema or writes.
    @param settings: Validated application account configuration.
    @return: Authenticated role, database, server version and whether the role is a superuser.
    """
    with transaction(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_user AS account, current_database() AS database_name, "
                "current_setting('server_version') AS server_version, "
                "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_superuser, %s AS probe",
                ("jukebox101_connection_check",),
            )
            return cursor.fetchone()
