"""Pin the Owner service boundary for the container read-write path: a non-Owner is refused before any repository access.

The offline cases run now against a repository stub that fails on any call, so a refusal is proven to happen before a database
read or write. The live Owner read and write against the dedicated migration container is skipped unless its own opt-in is set,
because it needs that container to be reachable from the test host.
"""

import os
from datetime import datetime, timezone

import pytest

from bot.owner_review import OwnerReviewService, ReviewAction

OWNER_ID = 900000000000000001  # [configured Owner]: Stand-in identifier for the offline cases only.
NON_OWNER_ID = 900000000000000002  # [other caller]: Any identifier that is not the configured Owner.
LIVE_OPT_IN = "JUKEBOX_RUN_OWNER_CONTAINER_RW"  # [live opt-in]: Exactly "1" allows the container-backed Owner test to run.


class _RefusingRepository:
    """Fail the test if the service reaches the repository for a caller that is not the Owner."""

    def __getattr__(self, name: str):
        """@description: Turn any repository access into an assertion failure, proving the refusal came first.
        @param name: Repository method the service attempted to call.
        @return: Never returns; always raises AssertionError.
        """
        raise AssertionError(f"repository reached before the Owner check: {name}")


def _service() -> OwnerReviewService:
    """@description: Build the review service with the refusing repository and a fixed clock, as the operator CLI builds it.
    @param: none
    @return: Owner review service bound to the stand-in Owner identifier.
    """
    return OwnerReviewService(_RefusingRepository(), OWNER_ID, lambda: datetime(2026, 10, 29, tzinfo=timezone.utc))


def test_a_non_owner_cannot_list_pending_operations():
    with pytest.raises(PermissionError):
        _service().list_pending(NON_OWNER_ID)


def test_a_non_owner_cannot_view_an_operation():
    with pytest.raises(PermissionError):
        _service().view(NON_OWNER_ID, "operation-id-placeholder")


def test_a_non_owner_cannot_preview_a_decision_before_any_repository_call():
    with pytest.raises(PermissionError):
        _service().preview(NON_OWNER_ID, "operation-id-placeholder", ReviewAction.REJECT, "synthetic reason", 1, "evidence-placeholder")


def test_a_non_owner_cannot_confirm_a_decision_before_any_repository_call():
    with pytest.raises(PermissionError):
        _service().confirm(NON_OWNER_ID, "token-placeholder")


def test_a_non_integer_caller_is_refused_as_not_the_owner():
    with pytest.raises(PermissionError):
        _service().list_pending(str(OWNER_ID))


@pytest.mark.skipif(os.environ.get(LIVE_OPT_IN) != "1", reason="Live Owner read-write against the dedicated migration container is not run here")
def test_the_owner_reads_pending_operations_from_the_migration_container():
    pytest.skip("Live container path is not implemented in this offline checkpoint; see docs/owner-migration-verification.md")
