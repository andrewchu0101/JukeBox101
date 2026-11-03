"""Real PostgreSQL behaviour of the Owner-first version rule and the explicit Owner conflict resolution.

Two production findings drive these tests: an automatic system version must never overwrite a version the Owner already confirmed, and a frozen
conflict (which conservatively freezes every other unsettled candidate) needs an audited Owner resolution that keeps the Owner's version.
"""

import pytest

from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.review import StaleReviewVersion
from bot.reward_candidates import RewardCandidateStatus
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, NOW, OWNER_ID, VIDEO_A, VIDEO_B, url
from tests.test_postgres_music_category_mode import MusicWorld

pytestmark = pytest.mark.postgres

OWNER_VERSION, OTHER_VERSION = "rv-owner-first", "rv-second-claim"


@pytest.fixture
def world(pg_database) -> MusicWorld:
    """@description: Provide one music_category world with settlement on.
    @param pg_database: Disposable database.
    @return: World.
    """
    return MusicWorld(pg_database)


def identity_of(world: MusicWorld, operation_id: str):
    """@description: Read the persisted identity of an operation's video through the Owner view.
    @param world: World.
    @param operation_id: Operation.
    @return: Current identity.
    """
    return world.repository.review_view(OWNER_ID, operation_id).identity


def owner_confirms(world: MusicWorld, operation: str, version: str, evidence_id: str) -> None:
    """@description: Run the Owner's version preview and confirmation with a unique evidence identifier, as the production card does.
    @param world: World.
    @param operation: Operation.
    @param version: Version reference the Owner asserts.
    @param evidence_id: Unique evidence identifier.
    @return: None.
    """
    projection = world.owner.view(OWNER_ID, operation)
    evidence = record_evidence(evidence_id, projection.review.video, EvidenceSourceType.OWNER_REVIEW, "https://example.test/manual-check", NOW,
                               "人工核對", OWNER_ID, RecordingVersionIdentity(version))
    preview = world.owner.preview_version_confirmation(OWNER_ID, operation, evidence, "人工核對", projection.review.version)
    world.owner.confirm(OWNER_ID, preview.token_id)


def owner_record(world: MusicWorld, operation: str, version: str) -> str:
    """@description: Find the stored Owner evidence of an operation's video that asserts one version.
    @param world: World.
    @param operation: Operation.
    @param version: Asserted version reference.
    @return: Evidence identifier.
    """
    records = world.repository.review_view(OWNER_ID, operation).evidence
    return next(record.evidence_id for record in records if record.version is not None and record.version.value == version)


def system_record(world: MusicWorld, operation: str) -> str:
    """@description: Find the stored system-judged evidence of an operation's video.
    @param world: World.
    @param operation: Operation.
    @return: Evidence identifier.
    """
    records = world.repository.review_view(OWNER_ID, operation).evidence
    return next(record.evidence_id for record in records if record.source_type.value == "system_judgement")


def conflicted_video(world: MusicWorld, user: int = ALICE, video: str = VIDEO_A, operation: str = "discord:1") -> tuple[str, str]:
    """@description: Create the production situation: an Owner confirmation, then a second contradictory Owner confirmation, on one pending video.
    @param world: World.
    @param user: Submitter.
    @param video: Video.
    @param operation: Operation key.
    @return: Operation key and the identifier of the Owner's first evidence record.
    """
    world.repository.accept_pending(user, url(video), operation)
    owner_confirms(world, operation, OWNER_VERSION, f"owner-first-{operation}")
    first = owner_record(world, operation, OWNER_VERSION)
    owner_confirms(world, operation, OTHER_VERSION, f"owner-second-{operation}")
    assert identity_of(world, operation).status is VersionIdentityStatus.CONFLICT
    return operation, first


def resolve(world: MusicWorld, operation: str, keep: str, decision: str = "resolve-1", requester: int = OWNER_ID):
    """@description: Ask the repository to resolve a conflict in favour of one stored Owner record.
    @param world: World.
    @param operation: Operation whose video is in conflict.
    @param keep: Evidence identifier to keep.
    @param decision: Decision key.
    @param requester: Caller.
    @return: Review after the resolution.
    """
    revision = world.repository.review_view(OWNER_ID, operation).review.version
    return world.repository.resolve_conflict(requester, decision, operation, keep, "以 Owner 手動確認的版本為準", revision)


def test_an_automatic_approval_never_overwrites_a_version_the_owner_already_confirmed(world) -> None:
    """@description: The production bug: Owner confirms first, auto review approves later; the Owner's version must stand and the song must be paid.
    @param world: Music-category world.
    @return: None.
    """
    world.repository.accept_pending(ALICE, url(VIDEO_A), "discord:1")
    owner_confirms(world, "discord:1", OWNER_VERSION, "owner-first-1")
    outcome = world.pipeline.process("discord:1")
    identity = identity_of(world, "discord:1")
    assert identity.status is VersionIdentityStatus.CONFIRMED and identity.version.value == OWNER_VERSION
    assert outcome.decision.status is RewardCandidateStatus.AWARDED and world.balances() == {ALICE: 1}
    assert admin_query(world.database, "SELECT 1 FROM jb2_evidence WHERE evidence_json::text LIKE '%%system_judgement%%'") == []
    world.assert_books_balance(1)


def test_without_an_owner_version_the_automatic_path_still_confirms_a_version_itself(world) -> None:
    """@description: The previous behaviour is unchanged for a video nobody confirmed.
    @param world: Music-category world.
    @return: None.
    """
    assert world.share(ALICE, VIDEO_A, "discord:1").decision.status is RewardCandidateStatus.AWARDED
    assert identity_of(world, "discord:1").status is VersionIdentityStatus.CONFIRMED


def test_one_conflicted_video_does_not_freeze_another_persons_later_submission(world) -> None:
    """@description: Owner ruling 2026-11-03: a conflict of unknown version freezes only its own submitter and video, so another person is still paid.
    @param world: Music-category world.
    @return: None.
    """
    conflicted_video(world)
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    assert outcome.decision.status is RewardCandidateStatus.AWARDED and world.balances() == {ALICE: 0, BOB: 1}


def test_the_owner_resolution_keeps_the_owner_version_and_unfreezes_every_innocent_candidate(world) -> None:
    """@description: After the resolution the conflicted song and the frozen bystander are both paid, once each, and the books balance.
    @param world: Music-category world.
    @return: None.
    """
    operation, keep = conflicted_video(world)
    world.share(BOB, VIDEO_B, "discord:2")
    resolve(world, operation, keep)
    identity = identity_of(world, operation)
    assert identity.status is VersionIdentityStatus.CONFIRMED and identity.version.value == OWNER_VERSION and identity.evidence_reference == keep
    world.pipeline.process(operation)  # [still pending validation]: The conflicted video is reviewed now that its identity is no longer frozen.
    world.pipeline.settle_ready()
    assert world.balances() == {ALICE: 1, BOB: 1}
    world.assert_books_balance(2)


def test_the_resolution_is_audited_and_replaying_it_changes_nothing(world) -> None:
    """@description: The same decision key returns the original result and writes nothing more.
    @param world: Music-category world.
    @return: None.
    """
    operation, keep = conflicted_video(world)
    first = resolve(world, operation, keep)
    again = world.repository.resolve_conflict(OWNER_ID, "resolve-1", operation, keep, "以 Owner 手動確認的版本為準", first.version - 1)
    assert again == first
    history = admin_query(world.database, "SELECT count(*) AS n FROM jb2_review_history WHERE decision_reason LIKE '%%以 Owner 手動確認的版本為準%%'")[0]["n"]
    assert history >= 1


def test_only_the_owner_may_resolve_and_nothing_changes_for_anyone_else(world) -> None:
    """@description: Authority is checked before any database work.
    @param world: Music-category world.
    @return: None.
    """
    operation, keep = conflicted_video(world)
    with pytest.raises(PermissionError):
        resolve(world, operation, keep, requester=ALICE)
    assert identity_of(world, operation).status is VersionIdentityStatus.CONFLICT


def test_a_system_record_cannot_be_the_one_to_keep(world) -> None:
    """@description: The kept record must be an Owner record; a system judgement never wins a resolution.
    @param world: Music-category world.
    @return: None.
    """
    world.repository.accept_pending(ALICE, url(VIDEO_A), "discord:1")
    owner_confirms(world, "discord:1", OWNER_VERSION, "owner-first-1")
    world.pipeline.process("discord:1")  # [Owner first]: Leaves no system record, so cite one that does not exist.
    with pytest.raises(ValueError):
        resolve(world, "discord:1", "no-such-evidence")


def test_a_video_that_is_not_in_conflict_cannot_be_resolved(world) -> None:
    """@description: The resolution acts on a conflict only.
    @param world: Music-category world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    keep = system_record(world, "discord:1")
    with pytest.raises(ValueError):
        resolve(world, "discord:1", keep)


def test_evidence_of_another_video_cannot_be_cited(world) -> None:
    """@description: A record must belong to the conflicted video.
    @param world: Music-category world.
    @return: None.
    """
    operation, _ = conflicted_video(world)
    world.repository.accept_pending(BOB, url(VIDEO_B), "discord:2")
    owner_confirms(world, "discord:2", "rv-bob", "owner-bob-2")
    foreign = owner_record(world, "discord:2", "rv-bob")
    with pytest.raises(ValueError):
        resolve(world, operation, foreign, decision="resolve-foreign")
    assert identity_of(world, operation).status is VersionIdentityStatus.CONFLICT


def test_a_stale_revision_is_refused(world) -> None:
    """@description: The Owner must have seen the current state.
    @param world: Music-category world.
    @return: None.
    """
    operation, keep = conflicted_video(world)
    with pytest.raises(StaleReviewVersion):
        world.repository.resolve_conflict(OWNER_ID, "resolve-stale", operation, keep, "以 Owner 手動確認的版本為準", 999)
    assert identity_of(world, operation).status is VersionIdentityStatus.CONFLICT
