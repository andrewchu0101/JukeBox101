"""Own serialized submission transactions through an injected connection factory.

Every writer locks the same PostgreSQL control row until commit; ambiguous commits require replay on a fresh connection.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
import sys

LOCK_WAIT = "2s"  # [bounded lock wait]: Transaction-local lock_timeout so blocked workers never wait indefinitely at the intake gate.


class CommitOutcomeUnknown(RuntimeError):
    """Require replay of the same durable operation key after a failed commit acknowledgement."""


class PersistenceNotReady(RuntimeError):
    """Block business work when the reviewed schema or historical bootstrap is unavailable."""


@contextmanager
def submission_transaction(factory: Callable[[], Any]) -> Iterator[tuple[Any, dict]]:
    """@description: Serialize new intake and settlement across processes using a database row lock.
    @param factory: Worker-local connection creator; never called at import or construction time.
    @return: Cursor and authoritative control state owned by this transaction.
    """
    # [owned connection]: No connection crosses workers or survives the operation.
    connection = factory()
    try:
        with connection.cursor() as cursor:
            _begin_locked(connection, cursor)
            # [serialization gate]: This lock is held through commit acknowledgement or connection cleanup.
            control = cursor.fetchone()
            if control is None or control["schema_version"] != "2b2-b1":
                raise PersistenceNotReady("Reviewed migration is required")
            yield cursor, control
        _commit(connection)
    except BaseException as error:
        _rollback(connection, error)
        raise
    finally:
        _close(connection, sys.exception())


def _begin_locked(connection: Any, cursor: Any) -> None:
    """@description: Read fresh committed state only after acquiring the cross-process gate.
    @param connection: Exclusively owned synchronous connection.
    @param cursor: Cursor on that connection.
    @return: None.
    """
    # psycopg opens the transaction implicitly with the first statement, so the isolation level is set first.
    cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    cursor.execute(f"SET LOCAL lock_timeout = '{LOCK_WAIT}'")  # [constant only]: LOCK_WAIT is a module constant, never input.
    cursor.execute("SELECT * FROM jb2_control WHERE singleton_id = 1 FOR UPDATE")


def _commit(connection: Any) -> None:
    """@description: Never classify a lost commit acknowledgement as a definite rollback.
    @param connection: Connection whose completed unit is ready to commit.
    @return: None.
    """
    try:
        connection.commit()
    except Exception as error:
        raise CommitOutcomeUnknown("Replay the same operation on a fresh connection") from error


def _rollback(connection: Any, primary: BaseException) -> None:
    """@description: Attempt cleanup without overwriting a primary business or ambiguous-commit exception.
    @param connection: Exclusively owned connection being abandoned.
    @param primary: Original exception that must retain its identity and outcome semantics.
    @return: None.
    """
    try:
        connection.rollback()
    except Exception:
        primary.add_note("Rollback failed; connection will be closed")


def _close(connection: Any, primary: BaseException | None) -> None:
    """@description: Preserve ambiguous commits and primary failures when connection cleanup also fails.
    @param connection: Exclusively owned connection.
    @param primary: Active primary exception, if any.
    @return: None.
    """
    try:
        connection.close()
    except Exception:
        if primary is not None:
            primary.add_note("Connection close failed; do not reuse this connection")
        else:
            raise CommitOutcomeUnknown("Connection cleanup failed; replay the same operation") from None
