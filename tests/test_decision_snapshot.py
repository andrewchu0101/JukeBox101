"""Prove Owner previews go stale when the identity, evidence or canonical mapping changes before confirmation.

Synthetic submissions over the offline SQL-protocol fake only; no Discord, MySQL or Gateway is involved.
Replay of an already-applied decision must still return its original result without writing again.
"""

from copy import deepcopy
from dataclasses import replace

import pytest

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_codec import decode_contract, encode_contract
from bot.evidence_record import EvidenceRecord
from bot.review import ApprovalSource, ReviewStatus, StaleReviewVersion
from bot.owner_review import ReviewAction
from bot.song_judgement import ContentJudgement
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus
from bot.decision_snapshot import DecisionSnapshot, require_unchanged_snapshot
from tests.intake_fixture import seed_intake
from tests.test_identity_service_path import (
    OWNER_ID,
    START,
    approve,
    confirm_version,
    owner_evidence,
    submission,
    video_of,
    world,  # noqa: F401  (pytest fixture imported by name)
)


def preview_approve(service, number: int) -> str:
    """@description: Preview an Owner approval for one synthetic submission and return its confirmation handle.
    @param service: Owner review service over the fake.
    @param number: Synthetic submission number.
    @return: Token identifier of the approval preview.
    """
    judgement = ContentJudgement(video_of(number), OWNER_ID, "synthetic song judgement", START, f"content-{number}", True)
    revision = service.view(OWNER_ID, f"op-{number}").review.version
    return service.preview(OWNER_ID, f"op-{number}", ReviewAction.APPROVE, "synthetic verified song", revision,
                           f"cite-{number}", content=judgement).token_id


def preview_reexamination(service, number: int, evidence_id: str, version: str):
    """@description: Preview a formal reexamination on approved or rejected synthetic submission with new reliable evidence.
    @param service: Owner review service over the fake.
    @param number: Synthetic submission number.
    @param evidence_id: Stable identifier of the new evidence.
    @param version: Version name the new evidence asserts.
    @return: Reexamination preview.
    """
    revision = service.view(OWNER_ID, f"op-{number}").review.version
    evidence = owner_evidence(video_of(number), evidence_id, version, hours=1)
    return service.preview_reexamination(OWNER_ID, f"op-{number}", evidence, "synthetic reexamination", revision)


def test_approve_preview_is_refused_after_a_version_confirmation_changes_the_identity(world):
    """@description: An approval previewed while the identity was unresolved must not apply after a version confirmation changed it.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    token = preview_approve(service, 1)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_reject_preview_is_refused_after_a_version_confirmation_changes_the_identity(world):
    """@description: A rejection previewed before a version confirmation must not apply against the changed identity.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "synthetic rejection", revision, "cite-reject").token_id
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_version_preview_is_refused_after_another_version_confirmation_changed_the_identity(world):
    """@description: A version preview made against an unresolved identity must not overwrite a version confirmed in the meantime.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview_version_confirmation(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-a", "orig-A"),
                                                 "synthetic version confirmation", revision).token_id
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-b", "orig-B", hours=1))
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_reexamination_preview_is_refused_after_a_version_confirmation_changes_the_identity(world):
    """@description: A reexamination previewed against an unresolved identity must not apply after the identity changed.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    approve(service, 1)
    token = preview_reexamination(service, 1, "ev-r", "orig-B").token_id
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A", hours=2))
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_reexamination_preview_shows_the_identity_that_confirmation_stores(world):
    """@description: A reexamination that will freeze a confirmed version previews that CONFLICT and stores exactly the previewed identity.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    approve(service, 1)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    preview = preview_reexamination(service, 1, "ev-b", "orig-B")
    assert preview.current_identity.status is VersionIdentityStatus.CONFIRMED
    assert preview.projected_identity is not None
    assert preview.projected_identity.status is VersionIdentityStatus.CONFLICT
    service.confirm(OWNER_ID, preview.token_id)
    assert service.view(OWNER_ID, "op-1").identity == preview.projected_identity


def test_version_preview_projection_is_what_confirmation_stores(world):
    """@description: A version confirmation preview names the identity the confirmation will store (guard against drift).
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    revision = service.view(OWNER_ID, "op-1").review.version
    preview = service.preview_version_confirmation(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-a", "orig-A"),
                                                   "synthetic version confirmation", revision)
    assert preview.current_identity.status is VersionIdentityStatus.UNRESOLVED
    service.confirm(OWNER_ID, preview.token_id)
    assert service.view(OWNER_ID, "op-1").identity == preview.projected_identity


def test_approve_preview_is_refused_after_supplementary_evidence_changes_the_evidence_set(world):
    """@description: Evidence added to the video after an approval preview makes that preview stale, even though no identity changed.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    token = preview_approve(service, 1)
    service.add_reexamination_evidence(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-n", "orig-A", hours=1),
                                       "synthetic supplement", "decision-supplement-1")
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_approve_preview_is_refused_when_a_stored_evidence_row_is_rewritten_under_the_same_identifier(world):
    """@description: A stored evidence row whose content changes while its identifier and row count stay the same still makes the preview stale.
    The real store is insert-only, so this simulates a rewrite the grants do not allow, to prove the snapshot does not rely on that.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    service.add_reexamination_evidence(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-dup", "orig-A", hours=1),
                                       "synthetic supplement", "decision-dup-1")
    token = preview_approve(service, 1)
    rows = database.state["jb2_evidence"]
    row = next(item for item in rows.values() if item["video_id"] == video_of(1).video_id and '"ev-dup"' in item["evidence_json"])
    rewritten = replace(decode_contract(row["evidence_json"], EvidenceRecord), version=RecordingVersionIdentity("orig-B"))
    row["evidence_json"] = encode_contract(rewritten)
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_approve_preview_is_refused_after_the_canonical_version_mapping_changes(world):
    """@description: Re-pointing a confirmed version to another canonical root makes an approval preview stale, even though its raw identity text is unchanged.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-z", "orig-Z"))
    token = preview_approve(service, 1)
    repository.merge("decision-merge-1", OWNER_ID, "synthetic evidence that both versions are one", "orig-A", "orig-Z")
    assert database.state["jb2_versions"]["orig-A"]["canonical_id"] == "orig-Z"
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_replayed_approval_with_the_same_key_returns_the_original_result_after_the_identity_changed(world):
    """@description: A repeated approval key replays the original result even when the identity has since changed, and writes nothing.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    judgement = ContentJudgement(video_of(1), OWNER_ID, "synthetic song judgement", START, "content-1", True)
    revision = service.view(OWNER_ID, "op-1").review.version
    snapshot = service.view(OWNER_ID, "op-1").snapshot
    command = ReviewCommand("decision-approve-1", "op-1", OWNER_ID, "synthetic verified song", revision,
                            approval_source=ApprovalSource.OWNER, song_eligible=True, evidence_reference="cite-1",
                            content_judgement=judgement, expected_snapshot=snapshot)
    first = repository.review(command)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    before = deepcopy(database.state)
    second = repository.review(command)
    assert second == first
    assert second.status is ReviewStatus.APPROVED
    assert database.state == before


def test_an_unrelated_case_decision_does_not_invalidate_an_approval_preview(world):
    """@description: A decision on another video moves the global control revision but leaves this preview valid (guard against over-invalidation).
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    token = preview_approve(service, 1)
    approve(service, 2)
    assert service.confirm(OWNER_ID, token).status is ReviewStatus.APPROVED


def test_a_reused_decision_key_with_a_different_previewed_snapshot_is_refused_as_different_intent(world):
    """@description: The previewed snapshot is part of the decision intent, so the same key with another snapshot cannot replay.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    judgement = ContentJudgement(video_of(1), OWNER_ID, "synthetic song judgement", START, "content-1", True)
    revision = service.view(OWNER_ID, "op-1").review.version
    snapshot = service.view(OWNER_ID, "op-1").snapshot
    command = ReviewCommand("decision-key-1", "op-1", OWNER_ID, "synthetic verified song", revision,
                            approval_source=ApprovalSource.OWNER, song_eligible=True, evidence_reference="cite-1",
                            content_judgement=judgement, expected_snapshot=snapshot)
    repository.review(command)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    other = replace(command, expected_snapshot=service.view(OWNER_ID, "op-1").snapshot)
    before = deepcopy(database.state)
    with pytest.raises(ValueError, match="different intent"):
        repository.review(other)
    assert database.state == before


def test_an_owner_confirmation_token_without_a_previewed_snapshot_cannot_build_any_write_command():
    """@description: The Owner service fails closed when a token lacks the snapshot its preview read, so no path can skip the S21 check.
    @param: none
    @return: None.
    """
    from datetime import datetime, timezone
    from bot.owner_review import _IssuedToken, _command_for, _reexamination_for, _version_confirmation_for
    issued = _IssuedToken(OWNER_ID, "op-1", ReviewAction.REJECT, "synthetic rejection", 0, "cite-reject", "decision-x",
                          datetime(2026, 10, 23, tzinfo=timezone.utc))
    for build in (_command_for, _reexamination_for, _version_confirmation_for):
        with pytest.raises(ValueError, match="snapshot its preview read"):
            build(issued)


def test_a_stale_preview_is_explained_to_the_owner_in_traditional_chinese_with_the_next_action():
    """@description: The Owner sees a Chinese explanation that tells them to preview again, not a raw English library message.
    @param: none
    @return: None.
    """
    from bot.discord_owner_review import _error_text
    from bot.review import StaleReviewVersion as Stale
    text = _error_text(Stale("Identity, canonical mapping or evidence changed after preview; preview the decision again"))
    assert "重新預覽" in text
    assert "Identity" not in text


def test_snapshot_refuses_any_difference_in_identity_canonical_version_or_evidence():
    """@description: The shared snapshot comparison refuses every changed fact and accepts an unset preview.
    @param: none
    @return: None.
    """
    video = video_of(1)
    base = DecisionSnapshot(_assignment(video), RecordingVersionIdentity("orig-A"), ("fp-a",))
    require_unchanged_snapshot(None, base)
    require_unchanged_snapshot(base, replace(base))
    with pytest.raises(StaleReviewVersion):
        require_unchanged_snapshot(base, replace(base, canonical_version=RecordingVersionIdentity("orig-Z")))
    with pytest.raises(StaleReviewVersion):
        require_unchanged_snapshot(base, replace(base, evidence_fingerprints=("fp-a", "fp-b")))
    with pytest.raises(StaleReviewVersion):
        require_unchanged_snapshot(base, replace(base, identity=_assignment(video, "orig-A")))


def _assignment(video, version: str | None = None):
    """@description: Build one confirmed or unresolved synthetic identity for snapshot comparisons.
    @param video: Video the identity belongs to.
    @param version: Confirmed version name, or None for an unresolved identity.
    @return: Version assignment.
    """
    from bot.version_identity import VersionAssignment
    if version is None:
        return VersionAssignment(video)
    return VersionAssignment(video, VersionIdentityStatus.CONFIRMED, RecordingVersionIdentity(version), "ev-a")




def test_approve_preview_is_refused_when_an_evidence_version_is_merged_into_another_canonical_root(world):
    """@description: A merge that re-roots an evidence record's asserted version changes how stored evidence resolves, so a preview made before it is stale.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    seed_intake(repository, submission(3))
    confirm_version(service, 3, owner_evidence(video_of(3), "ev-b0", "orig-B"))
    service.add_reexamination_evidence(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-b", "orig-B", hours=1),
                                       "synthetic supplement", "decision-evb-1")
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-z", "orig-Z"))
    token = preview_approve(service, 1)
    repository.merge("decision-merge-evb", OWNER_ID, "synthetic evidence that both versions are one", "orig-B", "orig-Z")
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_version_preview_with_new_evidence_is_refused_when_its_version_is_merged_before_confirmation(world):
    """@description: New evidence that is not yet stored resolves through canonical roots, so a merge after the preview must refuse the confirmation.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    seed_intake(repository, submission(3))
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-z", "orig-Z"))
    confirm_version(service, 3, owner_evidence(video_of(3), "ev-x0", "orig-X"))
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview_version_confirmation(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-x", "orig-X", hours=1),
                                                 "synthetic version confirmation", revision).token_id
    repository.merge("decision-merge-newx", OWNER_ID, "synthetic evidence that both versions are one", "orig-X", "orig-Z")
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_reexamination_preview_with_new_evidence_is_refused_when_its_version_is_merged_before_confirmation(world):
    """@description: A reexamination citing unstored evidence refuses confirmation when that evidence's version is merged after the preview.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    seed_intake(repository, submission(3))
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-z", "orig-Z"))
    confirm_version(service, 3, owner_evidence(video_of(3), "ev-x0", "orig-X"))
    approve(service, 1)
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview_reexamination(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-x", "orig-X", hours=1),
                                          "synthetic reexamination", revision).token_id
    repository.merge("decision-merge-newx-re", OWNER_ID, "synthetic evidence that both versions are one", "orig-X", "orig-Z")
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before
