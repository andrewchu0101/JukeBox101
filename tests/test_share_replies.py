"""Pin user-approved share copy and deterministic scenario precedence offline.

Presentation tests never instantiate Discord and do not change review or reward decisions.
"""

from dataclasses import replace

import pytest

from bot.share_replies import APPROVED_SHARE_REPLIES, ShareScenario, approved_share_reply, classify_share_scenario
from tests.version_candidate_support import make_candidate


@pytest.mark.parametrize("scenario,approved_text", [
    (ShareScenario.DUPLICATE_SELF, "又是這首？看來你真的很喜歡它！💿"),
    (ShareScenario.DUPLICATE_OTHER, "有人搶先一步把這首放進點唱機了！💿"),
    (ShareScenario.RETRY_SAME_OPERATION, "收到收到！不用連按兩次，我聽見了！🎵"),
    (ShareScenario.SAME_VERSION_DIFFERENT_VIDEO, "換了張封面，我還是認得這首歌！💿"),
    (ShareScenario.IDENTITY_UNRESOLVED, "這首歌好像有點耳熟……讓我確認一下！🔍"),
])
def test_only_exact_approved_text_is_available(scenario: ShareScenario, approved_text: str) -> None:
    """@description: Prevent unapproved copy additions or silent edits to accepted wording.
    @param scenario: User-approved business scenario.
    @param approved_text: Exact approved Traditional Chinese line.
    @return: None.
    """
    assert approved_share_reply(scenario) == approved_text
    assert len(APPROVED_SHARE_REPLIES) == len(ShareScenario) == 5


@pytest.mark.parametrize("same_user,expected", [(True, ShareScenario.DUPLICATE_SELF), (False, ShareScenario.DUPLICATE_OTHER)])
def test_exact_video_duplicate_context(same_user: bool, expected: ShareScenario) -> None:
    """@description: Separate self repetition from another member's earlier share.
    @param same_user: Whether the current submitter is the earlier contributor.
    @param expected: Approved duplicate scenario.
    @return: None.
    """
    # [prior share]: Exact video identity anchors the duplicate context.
    earlier = make_candidate(1)
    # [current share]: A new operation differs from a request retry.
    current = make_candidate(2, video_id=earlier.identity.video.video_id, submitter_id=1 if same_user else 2)
    assert classify_share_scenario(current, (earlier,)) is expected


def test_same_version_different_video_context() -> None:
    """@description: Present confirmed version aliases without deriving them from cover or title text.
    @param: none
    @return: None.
    """
    assert classify_share_scenario(make_candidate(2), (make_candidate(1),)) is ShareScenario.SAME_VERSION_DIFFERENT_VIDEO


def test_retry_precedes_unresolved_identity_and_duplicates() -> None:
    """@description: Keep repeated operation acknowledgment independent of subsequent review progress.
    @param: none
    @return: None.
    """
    # [same request]: Repeated evaluation does not create a second accepted operation.
    current = make_candidate(1, None)
    assert classify_share_scenario(current, (current,)) is ShareScenario.RETRY_SAME_OPERATION


def test_unknown_identity_never_gets_a_duplicate_version_claim() -> None:
    """@description: Use the unresolved scenario until authoritative mapping evidence exists.
    @param: none
    @return: None.
    """
    assert classify_share_scenario(make_candidate(2, None), (make_candidate(1),)) is ShareScenario.IDENTITY_UNRESOLVED


def test_distinct_version_and_future_share_have_no_duplicate_copy() -> None:
    """@description: Avoid presenting approved duplicate lines for a genuinely new version or future event.
    @param: none
    @return: None.
    """
    assert classify_share_scenario(make_candidate(2, "remix"), (make_candidate(1),)) is None
    assert classify_share_scenario(make_candidate(1), (make_candidate(2),)) is None


def test_exact_self_duplicate_precedes_cross_video_alias() -> None:
    """@description: Make overlapping scenario selection deterministic regardless of history ordering.
    @param: none
    @return: None.
    """
    # [current subject]: Both history forms refer to the same confirmed version.
    current = make_candidate(3, submitter_id=1)
    # [self history]: Exact video repetition takes precedence over a different-video alias.
    exact = make_candidate(1, video_id=current.identity.video.video_id, submitter_id=1)
    # [alias history]: An independently confirmed video of the same recording.
    alias = make_candidate(2)
    assert classify_share_scenario(current, (exact, alias)) is ShareScenario.DUPLICATE_SELF
    assert classify_share_scenario(current, (alias, exact)) is ShareScenario.DUPLICATE_SELF


def test_replayed_operation_cannot_change_subject() -> None:
    """@description: Refuse an idempotency key reused for another claimant or video.
    @param: none
    @return: None.
    """
    # [original request]: Its operation identity belongs to one submitter and video.
    original = make_candidate(1)
    with pytest.raises(ValueError):
        classify_share_scenario(replace(original, submitter_id=99), (original,))
    with pytest.raises(ValueError):
        classify_share_scenario(replace(original, accepted_sequence=2), (original,))


def test_approved_copy_is_read_only() -> None:
    """@description: Prevent a caller from modifying the approved reply catalog in place.
    @param: none
    @return: None.
    """
    with pytest.raises(TypeError):
        APPROVED_SHARE_REPLIES[ShareScenario.DUPLICATE_SELF] = "unapproved"


@pytest.mark.parametrize("conflict_field", ["submitter", "video", "sequence"])
@pytest.mark.parametrize("matching_first", [True, False])
def test_conflicting_replay_history_is_rejected_in_any_order(conflict_field: str, matching_first: bool) -> None:
    """@description: Reject contradictory operation history even after a matching record.
    @param conflict_field: Immutable operation attribute contradicted by one history record.
    @param matching_first: Whether the valid replay precedes the contradictory record.
    @return: None.
    """
    # [original operation]: Every matching operation record must preserve these accepted attributes.
    original = make_candidate(1)
    # [conflicting history]: Each variant changes exactly one immutable operation attribute.
    conflicting = {
        "submitter": replace(original, submitter_id=99),
        "video": make_candidate(1, video_id="video000002"),
        "sequence": replace(original, accepted_sequence=2),
    }[conflict_field]
    # [history ordering]: Both permutations must reject the same contradiction.
    history = (original, conflicting) if matching_first else (conflicting, original)
    with pytest.raises(ValueError):
        classify_share_scenario(original, history)


@pytest.mark.parametrize("copy_count", [2, 3])
def test_multiple_identical_operation_records_are_retries(copy_count: int) -> None:
    """@description: Preserve retry classification when all operation history records agree.
    @param copy_count: Number of identical accepted-operation records in the trusted history.
    @return: None.
    """
    # [replayed operation]: Unresolved identity does not change technical retry precedence.
    original = make_candidate(1, None)
    assert classify_share_scenario(original, (original,) * copy_count) is ShareScenario.RETRY_SAME_OPERATION


def test_new_operation_repeat_is_not_technical_retry() -> None:
    """@description: Distinguish a deliberate repeat share from resending the same operation.
    @param: none
    @return: None.
    """
    # [earlier share]: The same member already contributed this exact video.
    earlier = make_candidate(1)
    # [deliberate repeat]: A newly accepted operation retains duplicate-share semantics.
    current = make_candidate(2, video_id=earlier.identity.video.video_id, submitter_id=earlier.submitter_id)
    assert current.operation_id != earlier.operation_id
    assert classify_share_scenario(current, (earlier,)) is ShareScenario.DUPLICATE_SELF
