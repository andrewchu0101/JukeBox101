"""Run the retention purge in short transactions and record each run, so a stopped or failing job is visible from the database alone.

Inputs are a connection factory; output is the number of observations whose official values were really set to NULL.
"""

import logging
from collections.abc import Callable
from typing import Any

from bot.database.api_observations import finish_run, purge_pass, start_run
from bot.database.errors import DatabaseError
from bot.database.submission_transaction import submission_transaction

LOGGER = logging.getLogger(__name__)  # [diagnostics]: Counts and exception class names only.
MAX_PASSES = 100  # [bounded run]: A run clears at most this many batches; the next run continues.


def run_purge(connection_factory: Callable[[], Any]) -> int:
    """@description: Clear expired and completed observations batch by batch, always closing the run record with the outcome.
    @param connection_factory: Worker-local connection creator, as the repository uses.
    @return: Number of observations cleared by this run.
    """
    with submission_transaction(connection_factory) as (cursor, _control):
        run_id = start_run(cursor)
    cleared, error_class = 0, None  # [run result]: Count so far and the failure class, if any.
    try:
        for _ in range(MAX_PASSES):
            with submission_transaction(connection_factory) as (cursor, _control):
                batch = purge_pass(cursor)
            cleared += batch
            if batch == 0:
                break
    except Exception as error:  # noqa: BLE001 - the run must be closed with its class, then the failure propagates unchanged.
        error_class = type(error).__name__
        raise
    finally:
        _close_run(connection_factory, run_id, cleared, error_class)
    return cleared


def _close_run(connection_factory: Callable[[], Any], run_id: int, cleared: int, error_class: str | None) -> None:
    """@description: Record the run outcome without letting a second failure hide the first.
    @param connection_factory: Worker-local connection creator.
    @param run_id: Run opened by run_purge.
    @param cleared: Observations cleared so far.
    @param error_class: Failure class of the run, or None.
    @return: None.
    """
    try:
        with submission_transaction(connection_factory) as (cursor, _control):
            finish_run(cursor, run_id, cleared, error_class)
    except DatabaseError:
        LOGGER.error("Retention run %s could not be closed; the unfinished record shows the failure", run_id)
