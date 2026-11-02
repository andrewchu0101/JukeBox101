"""Verify the pipeline step that compares a new, otherwise approvable submission with the most recent existing songs.

The repository and the official provider are scripted fakes that record call order and quota units; nothing reaches a database or the network.
The comparison runs only for a submission that would be approved automatically, only when the independent switch is on, and never writes
the old videos' official values anywhere. API trouble writes nothing and is retried later (it is neither an approval nor a rejection).
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from bot.auto_pipeline import COMPARISON_RECENT_LIMIT, RETRY_BACKOFF_SECONDS, SWEEP_LOOKUP_LIMIT, AutoPipeline
from bot.auto_review import AutoReviewKind
from bot.auto_startup import build_auto_pipeline, load_auto_settings
from bot.database.auto_review_store import AutoReviewSubject
from bot.database.errors import DatabaseError
from bot.version_concerns import ConcernCode
from bot.youtube_metadata import BATCH_LOOKUP_SIZE, BatchLookup, BatchStatus, LookupStatus, MetadataLookup, VideoMetadata

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
TRUSTED, OTHER = "UC" + "a" * 22, "UC" + "b" * 22
TITLE_CANARY = "TitleCanaryZq9"
UNRELATED_TITLE = "Zzzz Qqqq 1234"


@dataclass(frozen=True)
class Recent:
    """Stand in for the repository's read-only comparison description."""

    video_ids: tuple[str, ...]
    last_accepted_sequence: int


def meta(video_id: str, title: str = "Alpha Beta Gamma", *, channel: str = TRUSTED, seconds: int = 189) -> VideoMetadata:
    """@description: Build official metadata for a trusted-channel music video.
    @param video_id: Identifier.
    @param title: Official title.
    @param channel: Channel.
    @param seconds: Duration.
    @return: Metadata.
    """
    return VideoMetadata(video_id, channel, "10", title, "none", False, "processed", "public", seconds, NOW)


def old_ids(count: int) -> tuple[str, ...]:
    """@description: Build distinct identifiers of existing songs.
    @param count: How many.
    @return: Identifiers.
    """
    return tuple(f"old{index:08d}" for index in range(count))


class FakeProvider:
    """Answer single and batched lookups from scripts and count quota units (one per request)."""

    def __init__(self, candidates: dict[str, VideoMetadata], batch_status=BatchStatus.COMPLETE, similar_title: str | None = None) -> None:
        """@description: Script the candidates and the comparison outcome.
        @param self: Fake provider.
        @param candidates: Video identifier to its official metadata.
        @param batch_status: Status every batch returns.
        @param similar_title: Title given to every existing song, or None for unrelated titles.
        @return: None.
        """
        self.candidates, self.batch_status, self.similar_title = candidates, batch_status, similar_title
        self.units = 0
        self.events: list[str] = []
        self.batch_requests: list[tuple[str, ...]] = []

    def lookup(self, video_id: str) -> MetadataLookup:
        """@description: Answer one candidate lookup.
        @param self: Fake provider.
        @param video_id: Candidate.
        @return: Found metadata.
        """
        self.units += 1
        self.events.append("lookup")
        return MetadataLookup(LookupStatus.FOUND, self.candidates[video_id])

    def lookup_many(self, video_ids) -> BatchLookup:
        """@description: Answer one comparison batch.
        @param self: Fake provider.
        @param video_ids: Existing songs to compare against.
        @return: Scripted batch.
        """
        self.units += math.ceil(len(video_ids) / BATCH_LOOKUP_SIZE)
        self.events.append("lookup_many")
        self.batch_requests.append(tuple(video_ids))
        if self.batch_status is not BatchStatus.COMPLETE:
            return BatchLookup(self.batch_status, ())
        title = self.similar_title or UNRELATED_TITLE
        return BatchLookup(BatchStatus.COMPLETE, tuple(meta(video_id, title, channel=OTHER, seconds=300) for video_id in video_ids))


class FakeRepository:
    """Serve pending operations and record every call in order."""

    def __init__(self, operations: dict[str, str], recent: Recent, comparison_error: Exception | None = None) -> None:
        """@description: Script the pending operations and the comparison read.
        @param self: Fake repository.
        @param operations: Operation to video identifier.
        @param recent: Comparison description returned for every read.
        @param comparison_error: Failure raised by the comparison read, or None.
        @return: None.
        """
        self.operations, self.recent, self.comparison_error = operations, recent, comparison_error
        self.events: list[str] = []
        self.applied: list[tuple[str, object]] = []
        self.comparison_requests: list[tuple[str, int]] = []

    def pending_validation_operations(self, limit, excluded=()):
        """@description: List the pending operations that are not paused.
        @param self: Fake repository.
        @param limit: Read bound.
        @param excluded: Paused videos.
        @return: Operation and video pairs.
        """
        return [(operation, video) for operation, video in self.operations.items() if video not in excluded][:limit]

    def auto_review_subject(self, operation_id):
        """@description: Describe the operation as awaiting validation.
        @param self: Fake repository.
        @param operation_id: Operation.
        @return: Subject.
        """
        self.events.append("subject")
        return AutoReviewSubject(self.operations[operation_id], 1, True)

    def recent_comparison(self, exclude_video_id, limit):
        """@description: Return the scripted comparison set or fail.
        @param self: Fake repository.
        @param exclude_video_id: Candidate, never compared with itself.
        @param limit: Requested size.
        @return: Comparison description.
        """
        self.events.append("recent_comparison")
        self.comparison_requests.append((exclude_video_id, limit))
        if self.comparison_error is not None:
            raise self.comparison_error
        return self.recent

    def apply_auto_review(self, operation_id, expected_revision, verdict, retain_api_values=False):
        """@description: Record the verdict that would be written.
        @param self: Fake repository.
        @param operation_id: Operation.
        @param expected_revision: Revision the lookup was made for.
        @param verdict: Verdict to apply.
        @param retain_api_values: Retention mode flag.
        @return: None.
        """
        self.events.append("apply")
        self.applied.append((operation_id, verdict))

    def ready_operations(self):
        """@description: Report nothing ready.
        @param self: Fake repository.
        @return: Empty list.
        """
        return []

    def revalidation_operations(self, limit, excluded=()):
        """@description: Report nothing to revalidate.
        @param self: Fake repository.
        @param limit: Read bound.
        @param excluded: Paused videos.
        @return: Empty list.
        """
        return []


def build(operations, provider, recent=None, *, confirm=True, allowlist=frozenset({TRUSTED}), clock=lambda: 0.0, **repository_options):
    """@description: Build a pipeline with the comparison switch as requested.
    @param operations: Pending operations.
    @param provider: Fake provider.
    @param recent: Comparison description.
    @param confirm: The independent version-confirmation switch.
    @param allowlist: Trusted channels.
    @param clock: Monotonic clock.
    @param repository_options: Extra repository script options.
    @return: Pipeline and repository.
    """
    repository = FakeRepository(operations, recent or Recent(old_ids(3), 41), **repository_options)
    pipeline = AutoPipeline(repository, provider, allowlist, settlement_enabled=False, api_retention=True, version_confirm=confirm, monotonic=clock)
    return pipeline, repository


ONE = {"discord:1": "cand0000001"}


def one_candidate(**provider_options):
    """@description: Provider with a single clean trusted candidate.
    @param provider_options: Provider script options.
    @return: Provider.
    """
    return FakeProvider({"cand0000001": meta("cand0000001")}, **provider_options)


def test_a_clean_candidate_is_compared_and_applied_with_a_confirmation_basis() -> None:
    """@description: The all-clear path ends in one automatic approval carrying what the comparison saw.
    @param: none
    @return: None.
    """
    provider = one_candidate()
    pipeline, repository = build(ONE, provider, Recent(old_ids(3), 41))
    pipeline.sweep()
    operation, verdict = repository.applied[0]
    assert (operation, verdict.kind, verdict.concerns) == ("discord:1", AutoReviewKind.AUTO_APPROVE, ())
    assert verdict.version_basis.last_accepted_sequence == 41 and verdict.version_basis.compared_count == 3


def test_the_comparison_reads_at_most_five_hundred_songs_excluding_the_candidate_itself() -> None:
    """@description: The recent window is a named constant of 500 and never includes the new video.
    @param: none
    @return: None.
    """
    assert COMPARISON_RECENT_LIMIT == 500
    pipeline, repository = build(ONE, one_candidate())
    pipeline.sweep()
    assert repository.comparison_requests == [("cand0000001", COMPARISON_RECENT_LIMIT)]


def test_the_provider_is_asked_about_exactly_the_songs_the_repository_returned() -> None:
    """@description: 120 existing songs become chunks totalling 120 identifiers in order.
    @param: none
    @return: None.
    """
    provider = one_candidate()
    pipeline, _repository = build(ONE, provider, Recent(old_ids(120), 7))
    pipeline.sweep()
    assert [video for request in provider.batch_requests for video in request] == list(old_ids(120))


def test_calls_happen_in_order_with_no_write_before_the_comparison_finishes() -> None:
    """@description: Lookup, comparison read and batch all precede the single write, so no transaction is open during network calls.
    @param: none
    @return: None.
    """
    provider = one_candidate()
    pipeline, repository = build(ONE, provider)
    pipeline.sweep()
    assert repository.events == ["subject", "recent_comparison", "apply"] and provider.events == ["lookup", "lookup_many"]


def test_with_the_switch_off_there_is_no_comparison_and_the_old_behaviour_remains() -> None:
    """@description: Off means off: no repository read, no batch, an approval without a basis.
    @param: none
    @return: None.
    """
    provider = one_candidate()
    pipeline, repository = build(ONE, provider, confirm=False)
    pipeline.sweep()
    assert repository.comparison_requests == [] and provider.batch_requests == []
    assert repository.applied[0][1].kind is AutoReviewKind.AUTO_APPROVE and repository.applied[0][1].version_basis is None


def test_a_candidate_that_would_not_be_approved_spends_no_comparison_quota() -> None:
    """@description: Untrusted channels and empty allowlists go to the Owner without any extra API call.
    @param: none
    @return: None.
    """
    for allowlist in (frozenset(), frozenset({OTHER})):
        provider = one_candidate()
        pipeline, repository = build(ONE, provider, allowlist=allowlist)
        pipeline.sweep()
        assert provider.batch_requests == [] and repository.comparison_requests == []
        assert repository.applied[0][1].kind is AutoReviewKind.DURATION_ONLY and repository.applied[0][1].version_basis is None


def test_an_empty_allowlist_never_approves_even_when_nothing_is_comparable() -> None:
    """@description: Fail closed: no trusted channel means no automatic approval and no version confirmation.
    @param: none
    @return: None.
    """
    pipeline, repository = build(ONE, one_candidate(), Recent((), 0), allowlist=frozenset())
    pipeline.sweep()
    assert repository.applied[0][1].kind is not AutoReviewKind.AUTO_APPROVE


def test_the_first_song_with_nothing_to_compare_is_approved_without_a_batch_call() -> None:
    """@description: An empty comparison set costs no quota and does not block the first submission.
    @param: none
    @return: None.
    """
    provider = one_candidate()
    pipeline, repository = build(ONE, provider, Recent((), 0))
    pipeline.sweep()
    assert provider.batch_requests == [] and repository.applied[0][1].kind is AutoReviewKind.AUTO_APPROVE


def test_a_similar_existing_song_sends_the_case_to_the_owner_with_its_reason() -> None:
    """@description: A concern turns the approval into an Owner review and withholds the confirmation basis.
    @param: none
    @return: None.
    """
    pipeline, repository = build(ONE, one_candidate(similar_title="Alpha Beta Gamma"))
    pipeline.sweep()
    verdict = repository.applied[0][1]
    assert verdict.kind is AutoReviewKind.DURATION_ONLY and verdict.version_basis is None
    assert ConcernCode.SIMILAR_TITLE in {concern.code for concern in verdict.concerns}


@pytest.mark.parametrize("status", [BatchStatus.QUOTA_EXHAUSTED, BatchStatus.INCOMPLETE])
def test_api_trouble_writes_nothing_and_is_retried_after_the_pause(status) -> None:
    """@description: Quota or API failure neither approves, rejects nor queues for review; the same case is asked again after 30 minutes.
    @param status: Failing comparison status.
    @return: None.
    """
    now = [0.0]
    provider = one_candidate(batch_status=status)
    pipeline, repository = build(ONE, provider, clock=lambda: now[0])
    pipeline.sweep()
    assert repository.applied == [] and provider.units > 0
    spent = provider.units
    pipeline.sweep()
    assert provider.units == spent and repository.applied == []
    now[0] = RETRY_BACKOFF_SECONDS + 1
    provider.batch_status = BatchStatus.COMPLETE
    pipeline.sweep()
    assert len(repository.applied) == 1 and repository.applied[0][1].kind is AutoReviewKind.AUTO_APPROVE


def test_a_failed_comparison_read_is_isolated_and_writes_nothing() -> None:
    """@description: A database failure while reading the comparison set leaves the case undecided and does not stop other cases.
    @param: none
    @return: None.
    """
    operations = {"discord:1": "cand0000001", "discord:2": "cand0000002"}
    provider = FakeProvider({"cand0000001": meta("cand0000001"), "cand0000002": meta("cand0000002")})
    pipeline, repository = build(operations, provider, comparison_error=DatabaseError("synthetic"))
    pipeline.sweep()
    assert repository.applied == [] and len(repository.comparison_requests) == 2


def test_a_sweep_never_spends_more_quota_units_than_its_budget() -> None:
    """@description: Each full comparison costs 1 + 10 units, so a sweep must stop starting cases when the next one no longer fits.
    @param: none
    @return: None.
    """
    count = SWEEP_LOOKUP_LIMIT // (1 + COMPARISON_RECENT_LIMIT // BATCH_LOOKUP_SIZE) + 3
    operations = {f"discord:{index}": f"cand{index:07d}" for index in range(count)}
    provider = FakeProvider({video: meta(video) for video in operations.values()})
    pipeline, repository = build(operations, provider, Recent(old_ids(COMPARISON_RECENT_LIMIT), 9))
    pipeline.sweep()
    assert 0 < provider.units <= SWEEP_LOOKUP_LIMIT and 0 < len(repository.applied) < count


def test_leftover_cases_are_handled_by_the_next_sweep_not_dropped_or_approved_blindly() -> None:
    """@description: What did not fit in one sweep is reviewed completely in a later one.
    @param: none
    @return: None.
    """
    count = SWEEP_LOOKUP_LIMIT // (1 + COMPARISON_RECENT_LIMIT // BATCH_LOOKUP_SIZE) + 3
    operations = {f"discord:{index}": f"cand{index:07d}" for index in range(count)}
    provider = FakeProvider({video: meta(video) for video in operations.values()})
    pipeline, repository = build(operations, provider, Recent(old_ids(COMPARISON_RECENT_LIMIT), 9))
    for _ in range(count):
        pipeline.sweep()
        repository.operations = {name: video for name, video in repository.operations.items() if name not in {item[0] for item in repository.applied}}
    assert {item[0] for item in repository.applied} == {f"discord:{index}" for index in range(count)}
    assert all(item[1].kind is AutoReviewKind.AUTO_APPROVE and item[1].version_basis for item in repository.applied)


def test_nothing_the_pipeline_hands_to_the_repository_contains_an_old_songs_title() -> None:
    """@description: Old videos' titles stay in memory; only codes, identifiers and numbers reach the repository.
    @param: none
    @return: None.
    """
    pipeline, repository = build(ONE, one_candidate(similar_title=TITLE_CANARY + " live"), Recent(old_ids(2), 3))
    pipeline.sweep()
    assert TITLE_CANARY not in repr(repository.applied) + repr(repository.recent)


def test_the_startup_builder_passes_the_independent_switch_to_the_pipeline() -> None:
    """@description: Only the explicit settings value turns the comparison on.
    @param: none
    @return: None.
    """
    environment = {"JUKEBOX_AUTO_REVIEW": "on", "JUKEBOX_API_RETENTION": "on", "YOUTUBE_API_KEY": "synthetic-key"}
    assert build_auto_pipeline(None, load_auto_settings(environment))._version_confirm is False
    assert build_auto_pipeline(None, load_auto_settings(dict(environment, JUKEBOX_AUTO_VERSION_CONFIRM="on")))._version_confirm is True
