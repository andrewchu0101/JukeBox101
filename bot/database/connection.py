"""Own synchronous MySQL connections and short transactional work units.

Validated settings create fresh DictCursor connections that are closed in their worker context.
"""

from contextlib import contextmanager
from collections.abc import Iterator

import pymysql
from pymysql.cursors import DictCursor

from bot.config import DatabaseSettings

CONNECTION_TIMEOUT_SECONDS = 5
QUERY_TIMEOUT_SECONDS = 10


def open_connection(settings: DatabaseSettings) -> pymysql.Connection:
    """@description: Give each worker an independent connection with explicit transaction control.
    @param settings: Validated application database credentials and destination.
    @return: New connection owned exclusively by its calling worker.
    """
    return pymysql.connect(
        host=settings.host,
        port=settings.port,
        user=settings.user,
        password=settings.password,
        database=settings.database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=CONNECTION_TIMEOUT_SECONDS,
        read_timeout=QUERY_TIMEOUT_SECONDS,
        write_timeout=QUERY_TIMEOUT_SECONDS,
        init_command="SET time_zone = '+00:00'",
    )


@contextmanager
def transaction(settings: DatabaseSettings) -> Iterator[pymysql.Connection]:
    """@description: Commit successful work and roll back failures before releasing resources.
    @param settings: Configuration used inside the calling worker thread.
    @return: Context yielding an exclusively owned transaction connection.
    """
    # [transaction ownership]: Connection creation, use and close stay in one worker.
    connection = open_connection(settings)
    try:
        connection.begin()
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def inspect_database_identity(settings: DatabaseSettings) -> dict:
    """@description: Verify the application destination without requiring schema or writes.
    @param settings: Validated application account configuration.
    @return: Authenticated account and selected database reported by MySQL.
    """
    with transaction(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT CURRENT_USER() AS account, DATABASE() AS database_name, %s AS probe",
                ("jukebox101_connection_check",),
            )
            return cursor.fetchone()
