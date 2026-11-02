"""Verify the pipeline in music_category mode: the stage A comparison still guards every approval, and the allowlist mode is untouched.

Fakes are the ones of test_auto_pipeline_version_confirm.py (scripted provider and repository); nothing reaches a database or the network.
"""

from dataclasses import replace

import pytest

from bot.auto_pipeline import COMPARISON_RECENT_LIMIT, AutoPipeline
from bot.auto_review import AutoReviewKind
from bot.version_concerns import ConcernCode
from bot.youtube_metadata import BatchStatus
from tests.test_auto_pipeline_version_confirm import ONE, FakeProvider, FakeRepository, Recent, meta, old_ids

OTHER_CHANNEL = "UC" + "q" * 22  # [not on any allowlist]: The pipeline is built with an empty allowlist.


def build(provider, recent=None, *, mode="music_category", confirm=True, allowlist=frozenset(), clock=lambda: 0.0):
    """@description: Build a pipeline with the requested trust mode and an empty allowlist.
    @param provider: Fake provider.
    @param recent: Comparison description.
    @param mode: Trust mode.
    @param confirm: The version-confirmation switch.
    @param allowlist: Allowlist, empty by default.
    @param clock: Monotonic clock.
    @return: Pipeline and repository.
    """
    repository = FakeRepository(ONE, recent or Recent(old_ids(3), 41))
    pipeline = AutoPipeline(repository, provider, allowlist, settlement_enabled=False, api_retention=True, version_confirm=confirm,
                            trust_mode=mode, monotonic=clock)
    return pipeline, repository


def untrusted_candidate(**overrides) -> FakeProvider:
    """@description: Provider whose single candidate sits on a channel that no list mentions.
    @param overrides: Provider options or metadata overrides under the key metadata.
    @return: Provider.
    """
    metadata = replace(meta("cand0000001"), channel_id=OTHER_CHANNEL, **overrides.pop("metadata", {}))
    return FakeProvider({"cand0000001": metadata}, **overrides)


def test_a_clean_song_from_an_unlisted_channel_is_compared_and_approved_with_a_confirmation_basis() -> None:
    """@description: The comparison runs exactly as in stage A and clears the song without any allowlist.
    @param: none
    @return: None.
    """
    provider = untrusted_candidate()
    pipeline, repository = build(provider, Recent(old_ids(3), 41))
    pipeline.sweep()
    verdict = repository.applied[0][1]
    assert verdict.kind is AutoReviewKind.AUTO_APPROVE and verdict.concerns == () and verdict.version_basis.last_accepted_sequence == 41
    assert repository.comparison_requests == [("cand0000001", COMPARISON_RECENT_LIMIT)] and provider.events == ["lookup", "lookup_many"]


def test_a_doubt_still_sends_the_case_to_the_owner_without_a_confirmation_basis() -> None:
    """@description: A similar existing song is a doubt regardless of channel trust.
    @param: none
    @return: None.
    """
    pipeline, repository = build(untrusted_candidate(similar_title="Alpha Beta Gamma"))
    pipeline.sweep()
    verdict = repository.applied[0][1]
    assert verdict.kind is AutoReviewKind.DURATION_ONLY and verdict.version_basis is None
    assert ConcernCode.SIMILAR_TITLE in {concern.code for concern in verdict.concerns}


@pytest.mark.parametrize("status", [BatchStatus.QUOTA_EXHAUSTED, BatchStatus.INCOMPLETE])
def test_api_trouble_during_the_comparison_writes_nothing_and_never_approves(status) -> None:
    """@description: The open mode must never approve on an incomplete comparison.
    @param status: Failing batch status.
    @return: None.
    """
    pipeline, repository = build(untrusted_candidate(batch_status=status))
    pipeline.sweep()
    assert repository.applied == []


def test_a_non_music_category_spends_no_comparison_quota_and_waits_for_the_owner() -> None:
    """@description: The category gate runs before any comparison call.
    @param: none
    @return: None.
    """
    provider = untrusted_candidate(metadata={"category_id": "24"})
    pipeline, repository = build(provider)
    pipeline.sweep()
    assert provider.batch_requests == [] and repository.comparison_requests == []
    assert repository.applied[0][1].kind is AutoReviewKind.DURATION_ONLY and "category_not_music" in repository.applied[0][1].reason


def test_the_first_song_with_nothing_to_compare_is_approved_without_a_batch_call() -> None:
    """@description: An empty pool costs no quota and does not block the very first submission.
    @param: none
    @return: None.
    """
    provider = untrusted_candidate()
    pipeline, repository = build(provider, Recent((), 0))
    pipeline.sweep()
    assert provider.batch_requests == [] and repository.applied[0][1].kind is AutoReviewKind.AUTO_APPROVE


def test_allowlist_mode_with_an_empty_list_is_unchanged_nothing_is_compared_or_approved() -> None:
    """@description: The same song in allowlist mode waits for the Owner and costs no comparison quota.
    @param: none
    @return: None.
    """
    provider = untrusted_candidate()
    pipeline, repository = build(provider, mode="allowlist")
    pipeline.sweep()
    assert provider.batch_requests == [] and repository.comparison_requests == []
    assert repository.applied[0][1].kind is AutoReviewKind.DURATION_ONLY and "channel_not_trusted" in repository.applied[0][1].reason


def test_the_default_mode_of_the_pipeline_is_allowlist() -> None:
    """@description: Callers that do not pass a mode keep today's behaviour.
    @param: none
    @return: None.
    """
    repository = FakeRepository(ONE, Recent(old_ids(1), 1))
    provider = untrusted_candidate()
    AutoPipeline(repository, provider, frozenset(), settlement_enabled=False, api_retention=True, version_confirm=True).sweep()
    assert repository.applied[0][1].kind is AutoReviewKind.DURATION_ONLY


def test_music_category_without_version_confirmation_is_refused_by_the_pipeline_itself() -> None:
    """@description: Defence in depth behind the startup check: the open mode cannot exist without the comparison.
    @param: none
    @return: None.
    """
    with pytest.raises(ValueError):
        AutoPipeline(FakeRepository(ONE, Recent((), 0)), untrusted_candidate(), frozenset(), settlement_enabled=False, api_retention=True,
                     version_confirm=False, trust_mode="music_category")


@pytest.mark.parametrize("mode", ["", "open", "MUSIC_CATEGORY", None])
def test_an_unknown_mode_is_refused_by_the_pipeline(mode) -> None:
    """@description: Only the two exact constants are accepted.
    @param mode: Invalid mode.
    @return: None.
    """
    with pytest.raises(ValueError):
        AutoPipeline(FakeRepository(ONE, Recent((), 0)), untrusted_candidate(), frozenset(), settlement_enabled=False, api_retention=True,
                     version_confirm=True, trust_mode=mode)


def test_nothing_handed_to_the_repository_contains_a_channel_id() -> None:
    """@description: The channel is judged in memory; the audit text carries a reason code only.
    @param: none
    @return: None.
    """
    pipeline, repository = build(untrusted_candidate(), Recent(old_ids(2), 3))
    pipeline.sweep()
    assert OTHER_CHANNEL not in repr(repository.applied[0][1].public_reason) + repr(repository.applied[0][1].concerns)
