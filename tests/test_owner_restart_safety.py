"""Verify that Owner previews and confirmation windows fail safely after a process restart.

Each scenario builds a fresh OwnerReviewService over the same fake database, which models a restarted process whose tokens are gone; no Discord, MySQL or Gateway is involved.
"""

from copy import deepcopy

import pytest

from bot.owner_review import OwnerReviewService, ReviewAction, UnknownReviewToken
from tests.test_owner_review_service import EVIDENCE_REFERENCE, OWNER_ID, TOKEN_TTL, seed_pending, world  # noqa: F401


def _restarted_service(repository, clock) -> OwnerReviewService:
    """@description: Model a restarted process by constructing a new coordinator with no token memory over the same repository.
    @param repository: Real repository that still reflects the committed database state.
    @param clock: Synthetic clock shared with the earlier process.
    @return: Fresh service whose in-memory token store is empty.
    """
    return OwnerReviewService(repository, OWNER_ID, clock, token_ttl=TOKEN_TTL)


def test_token_issued_before_restart_is_refused_without_any_write(world):
    """@description: A confirmation sent after a restart must not execute the old decision, because the token existed only in the previous process memory.
    @param world: Offline database, repository, service and clock fixture.
    @return: None.
    """
    database, repository, service, clock = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1, EVIDENCE_REFERENCE).token_id
    state_before_restart = deepcopy(database.state)
    restarted = _restarted_service(repository, clock)
    with pytest.raises(UnknownReviewToken):
        restarted.confirm(OWNER_ID, token)
    assert database.state == state_before_restart


def test_expired_preview_is_refused_after_restart_without_any_write(world):
    """@description: After restart and past the time limit, an old token is refused with the same safe outcome and writes nothing.
    @param world: Offline database, repository, service and clock fixture.
    @return: None.
    """
    database, repository, service, clock = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1, EVIDENCE_REFERENCE).token_id
    clock.advance(int(TOKEN_TTL.total_seconds()) + 1)
    state_before_restart = deepcopy(database.state)
    restarted = _restarted_service(repository, clock)
    with pytest.raises(UnknownReviewToken):
        restarted.confirm(OWNER_ID, token)
    assert database.state == state_before_restart


def test_a_new_preview_after_restart_is_confirmed_once_and_only_then_writes(world):
    """@description: After a restart the Owner may preview again; the new token behaves like any other, so the decision is written exactly once.
    @param world: Offline database, repository, service and clock fixture.
    @return: None.
    """
    database, repository, service, clock = world
    seed_pending(repository)
    service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1, EVIDENCE_REFERENCE)
    restarted = _restarted_service(repository, clock)
    history_before_confirm = deepcopy(database.state["jb2_review_history"])
    token = restarted.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1, EVIDENCE_REFERENCE).token_id
    assert database.state["jb2_review_history"] == history_before_confirm
    restarted.confirm(OWNER_ID, token)
    history_after_first_confirm = deepcopy(database.state["jb2_review_history"])
    assert len(history_after_first_confirm) == len(history_before_confirm) + 1
    restarted.confirm(OWNER_ID, token)
    assert database.state["jb2_review_history"] == history_after_first_confirm
