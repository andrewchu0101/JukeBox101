"""Persist new submission events and immutable receipts under the shared transaction gate.

Acceptance sequence is allocated transactionally and never inferred from AUTO_INCREMENT or Discord timestamps.
"""

from dataclasses import replace
from typing import Any

from bot.database.persisted_snapshot import _canonical, decode_current_identity, decode_current_review, fetch_all, load_snapshot
from bot.database.repository import _ensure_user, _upsert_pending_song
from bot.database.submission_codec import decode_contract, encode_contract
from bot.review import ReviewSnapshot
from bot.submission_workflow import SubmissionRequest, SubmissionResult, submit_offline
from bot.urls import parse_youtube_url
from bot.version_identity import VersionAssignment


def ensure_version(cursor: Any, identity: VersionAssignment) -> None:
    """@description: Register an explicitly evidenced version without guessing names or musical identity.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param identity: Trusted internal positive or conflicting assignment.
    @return: None.
    """
    if identity.version is None:
        return
    cursor.execute("INSERT INTO jb2_versions (version_id, canonical_id, evidence_reference) VALUES (%s,%s,%s) "
                   "ON DUPLICATE KEY UPDATE version_id = version_id",
                   (identity.version.value, identity.version.value, identity.evidence_reference))


def accept_submission(cursor: Any, control: dict, request: SubmissionRequest) -> SubmissionResult:
    """@description: Save one ordered event or replay the original receipt without applying retry changes.
    @param cursor: Cursor owning the global ordering gate.
    @param control: Locked committed acceptance counter and revision.
    @param request: URL and trusted evidence; caller sequence is replaced by database allocation.
    @return: Original receipt on retry or the newly persisted offline workflow result.
    """
    cursor.execute("SELECT receipt_json FROM jb2_receipts WHERE operation_id = %s", (request.operation_id,))
    # [original receipt]: A lost commit acknowledgement is resolved through the same unique operation key.
    previous = cursor.fetchone()
    if previous is not None:
        return _replay(cursor, control, request, previous)
    # [new intake order]: This counter update rolls back with the event and remains locked until commit.
    sequence = control["accepted_sequence"] + 1
    cursor.execute("UPDATE jb2_control SET accepted_sequence = %s, revision = revision + 1 WHERE singleton_id = 1", (sequence,))
    control = dict(control, accepted_sequence=sequence, revision=control["revision"] + 1)
    request = _prepare_video(cursor, replace(request, accepted_sequence=sequence))
    roots = {row["version_id"]: row["canonical_id"] for row in fetch_all(cursor, "SELECT version_id,canonical_id FROM jb2_versions")}  # [canonical projection]: Respect prior Owner merges.
    request = replace(request, identity=_canonical(request.identity, roots))
    # [new-operation scope]: The locked database counter independently expects this intake key.
    snapshot = load_snapshot(cursor, control)
    snapshot = replace(snapshot, manifest=replace(snapshot.manifest,
                       operation_ids=snapshot.manifest.operation_ids | {request.operation_id}))
    result = submit_offline(request, snapshot)
    _persist_event(cursor, result)
    return result


def _replay(cursor: Any, control: dict, request: SubmissionRequest, row: dict) -> SubmissionResult:
    """@description: Reuse Batch A technical-retry validation while preserving immutable original business semantics.
    @param cursor: Transaction cursor holding the writer gate.
    @param control: Locked read revision.
    @param request: Repeated operation with potentially different URL presentation.
    @param row: Trusted stored receipt.
    @return: Original business outcome with retry presentation and no new event.
    """
    # [persisted outcome]: Do not recompute the original result from newer review or catalog facts.
    original = decode_contract(row["receipt_json"], SubmissionResult)
    request = replace(request, accepted_sequence=original.candidate.accepted_sequence,
                      identity=original.candidate.identity, review=original.candidate.review)
    return submit_offline(request, load_snapshot(cursor, control), original_result=original)


def _prepare_video(cursor: Any, request: SubmissionRequest) -> SubmissionRequest:
    """@description: Reuse existing song rows while separating legacy membership from new acceptance events.
    @param cursor: Cursor holding the global gate before any legacy song lock.
    @param request: New ordered operation and trusted evidence.
    @return: Request bound to the authoritative current per-video review.
    """
    # [normalized video]: Source parsing remains the existing shared contract.
    video = parse_youtube_url(request.source_url)
    if video.video_id != request.identity.video.video_id:
        raise ValueError("Identity does not match submitted URL")
    cursor.execute("SELECT song_id FROM songs WHERE platform = %s AND platform_track_id = %s", ("youtube", video.video_id))
    existing = cursor.fetchone()  # [legacy membership]: Existing songs are never assigned invented historical order.
    _upsert_pending_song(cursor, video)
    cursor.execute("SELECT song_id FROM songs WHERE platform = %s AND platform_track_id = %s FOR UPDATE", ("youtube", video.video_id))
    song_id = cursor.fetchone()["song_id"]  # [legacy FK]: Existing ledger uniqueness remains intact.
    cursor.execute("SELECT video_id,identity_json,review_json,review_revision,review_status FROM jb2_videos WHERE video_id = %s", (video.video_id,))
    current = cursor.fetchone()  # [current projection]: Existing review cannot be replaced by caller claims.
    if current is not None:
        return _existing_video_request(cursor, request, current)
    if existing is not None:
        cursor.execute("INSERT INTO jb2_legacy (song_id) VALUES (%s) ON DUPLICATE KEY UPDATE song_id = song_id", (song_id,))
    return _insert_video(cursor, request, song_id)


def _existing_video_request(cursor: Any, request: SubmissionRequest, current: dict) -> SubmissionRequest:
    """@description: Accept deliberate repeat events without revalidating or overwriting an existing video's review.
    @param cursor: Serialized transaction cursor.
    @param request: New share operation, not a review-update command.
    @param current: Authoritative current identity and review projection.
    @return: Request reusing current review; new evidence must use audited review operations.
    """
    # [current facts]: A fresh share does not reopen a terminal review or reset its revision.
    identity = decode_current_identity(current)
    review = decode_current_review(current)
    roots = {row["version_id"]: row["canonical_id"] for row in fetch_all(cursor, "SELECT version_id,canonical_id FROM jb2_versions")}
    if _canonical(identity, roots) != _canonical(request.identity, roots):
        raise ValueError("Update version evidence through the audited review boundary")
    return replace(request, identity=identity, review=review, expected_review_version=review.version,
                   duration=None, validation_error=None)


def _insert_video(cursor: Any, request: SubmissionRequest, song_id: int) -> SubmissionRequest:
    """@description: Initialize a video with pending review rather than trusting caller-supplied approval.
    @param cursor: Current transaction cursor.
    @param request: Trusted identity assertion and new operation.
    @param song_id: Existing songs foreign key.
    @return: Request based on a fresh pending review revision.
    """
    ensure_version(cursor, request.identity)
    # [initial review]: Only actual pure transitions may produce approval after intake.
    review = ReviewSnapshot(request.identity.video)
    cursor.execute("INSERT INTO jb2_videos (video_id,song_id,identity_json,review_json,review_revision,review_status) "
                   "VALUES (%s,%s,%s,%s,%s,%s)", (request.identity.video.video_id, song_id,
                   encode_contract(request.identity), encode_contract(review), review.version, review.status.value))
    persist_review(cursor, review, request.submitter_id, "trusted initial pending video", request.identity)
    return replace(request, review=review)


def _persist_event(cursor: Any, result: SubmissionResult) -> None:
    """@description: Save the share, current review and original receipt atomically with its counter allocation.
    @param cursor: Transaction cursor holding the serialization gate.
    @param result: Pure Batch A outcome, never a payment.
    @return: None.
    """
    # [accepted candidate]: A distinct operation remains an event even when its recording is already known.
    candidate = result.candidate
    _ensure_user(cursor, candidate.submitter_id)
    cursor.execute("INSERT INTO jb2_events (operation_id,accepted_sequence,discord_user_id,video_id,source_url,reward_status) "
                   "VALUES (%s,%s,%s,%s,%s,%s)", (candidate.operation_id, candidate.accepted_sequence,
                   candidate.submitter_id, candidate.identity.video.video_id, result.video.source_url, result.reward.status.value))
    cursor.execute("INSERT INTO jb2_receipts (operation_id,receipt_json) VALUES (%s,%s)",
                   (candidate.operation_id, encode_contract(result)))
    cursor.execute("INSERT INTO jb2_reminders (video_id,next_due_at) SELECT video_id,accepted_at + INTERVAL 24 HOUR "
                   "FROM jb2_events WHERE operation_id=%s ON DUPLICATE KEY UPDATE next_due_at=jb2_reminders.next_due_at", (candidate.operation_id,))
    persist_review(cursor, candidate.review, candidate.submitter_id, "initial accepted submission", candidate.identity)


def persist_review(cursor: Any, review: ReviewSnapshot, actor_id: int, reason: str, identity: VersionAssignment) -> None:
    """@description: Maintain one current projection while appending immutable review and evidence history.
    @param cursor: Transaction-owned cursor.
    @param review: Validated current Hybrid Review revision.
    @param actor_id: Auditable trusted actor identifier, not authorization by itself.
    @param reason: Required human-readable decision explanation.
    @param identity: Original version assertion with provenance.
    @return: None.
    """
    cursor.execute("UPDATE jb2_videos SET review_json=%s, review_revision=%s, review_status=%s, identity_json=%s WHERE video_id=%s",
                   (encode_contract(review), review.version, review.status.value, encode_contract(identity), review.video.video_id))
    _append_review_revision(cursor, review, actor_id, reason)
    cursor.execute("INSERT INTO jb2_evidence (video_id,evidence_json,actor_id,decision_reason) VALUES (%s,%s,%s,%s)",
                   (review.video.video_id, encode_contract((identity, review)), actor_id, reason))


def _append_review_revision(cursor: Any, review: ReviewSnapshot, actor_id: int, reason: str) -> None:
    """@description: Preserve immutable review history instead of silently accepting contradictory revision reuse.
    @param cursor: Globally serialized transaction cursor.
    @param review: Current validated review revision.
    @param actor_id: Audited internal actor.
    @param reason: Decision explanation.
    @return: None.
    """
    cursor.execute("SELECT review_json FROM jb2_review_history WHERE video_id=%s AND revision=%s",
                   (review.video.video_id, review.version))
    previous = cursor.fetchone()  # [immutable revision]: The same revision may only replay identical facts.
    if previous is not None:
        if decode_contract(previous["review_json"], ReviewSnapshot) != review:
            raise ValueError("Review history revision contradicts current projection")
        return
    cursor.execute("INSERT INTO jb2_review_history (video_id,revision,review_json,actor_id,decision_reason) VALUES (%s,%s,%s,%s,%s)",
                   (review.video.video_id, review.version, encode_contract(review), actor_id, reason))
