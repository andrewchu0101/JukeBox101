"""Exercise the real Owner review service path under the application account on the disposable container database.

Read path only: the Owner pending list is served by OwnerReviewService over SubmissionRepository, the same construction
the Owner CLI uses. Write paths (approve, confirm) need a fully prepared review case and are NOT covered here.
Runs only in container test mode with the disposable credential supplied through the process environment.
"""

import os
from datetime import datetime, timedelta, timezone
from functools import partial

import pytest

from bot.config import DatabaseSettings
from bot.database.connection import open_connection
from bot.database.submission_repository import SubmissionRepository
from bot.owner_review import OwnerReviewService

CONTAINER_MODE = os.environ.get("JUKEBOX_RUN_CONTAINER_MYSQL") == "1"
CONTAINER_PASSWORD = os.environ.get("TEST_CONTAINER_DB_PASSWORD", "")
requires_container = pytest.mark.skipif(not (CONTAINER_MODE and CONTAINER_PASSWORD), reason="container test mode not enabled")
OWNER_ID = 900000000000000042  # [fixture owner]: A test identity on the disposable schema, never a real account.


@requires_container
def test_owner_pending_list_reads_through_the_real_service_with_the_application_account():
    settings = DatabaseSettings("127.0.0.1", 3307, "jukebox101_app", CONTAINER_PASSWORD, "jukebox101_test")
    repository = SubmissionRepository(partial(open_connection, settings), OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: datetime.now(timezone.utc), token_ttl=timedelta(minutes=5))
    page = service.list_pending(OWNER_ID)
    assert page is not None


@requires_container
def test_a_non_owner_is_refused_by_the_service_before_any_read():
    settings = DatabaseSettings("127.0.0.1", 3307, "jukebox101_app", CONTAINER_PASSWORD, "jukebox101_test")
    repository = SubmissionRepository(partial(open_connection, settings), OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: datetime.now(timezone.utc), token_ttl=timedelta(minutes=5))
    with pytest.raises(PermissionError):
        service.list_pending(OWNER_ID + 1)
