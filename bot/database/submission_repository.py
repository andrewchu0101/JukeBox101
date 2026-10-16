"""Expose worker-local persisted submission, review and settlement operations.

Construction performs no I/O; an explicit injected connection factory is invoked only by authorized future callers.
"""

from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from typing import Any

from bot.database.persisted_snapshot import decode_current_identity, load_snapshot
from bot.database.review_persistence import ReviewCommand, apply_review, require_owner
from bot.database.reward_settlement import settle_reward
from bot.database.submission_maintenance import merge_versions, pending_summary, store_exclusion
from bot.database.submission_store import accept_submission
from bot.database.submission_transaction import PersistenceNotReady, submission_transaction
from bot.models import HISTORY_LIMIT
from bot.review import ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.urls import parse_youtube_url
from bot.reward_candidates import RewardDecision
from bot.submission_snapshot import SubmissionSnapshot
from bot.submission_workflow import SubmissionRequest, SubmissionResult
from bot.version_identity import VersionAssignment, VersionExclusion, require_reference


class SubmissionRepository:
    """Own complete synchronous transactions without changing the existing Discord-facing MusicRepository."""

    def __init__(self, connection_factory: Callable[[], Any], owner_discord_user_id: int) -> None:
        """@description: Keep connection creation deferred and Owner identity outside user-controlled requests.
        @param self: New persistent submission boundary.
        @param connection_factory: Existing worker-local PyMySQL creator or offline fake.
        @param owner_discord_user_id: Deployment-configured Owner Discord user ID, not a role or nickname.
        @return: None.
        """
        if type(owner_discord_user_id) is not int or owner_discord_user_id <= 0:
            raise ValueError("A configured Owner identity is required")
        self._factory = connection_factory  # [worker connection]: Never retain or share a live connection.
        self._owner_id = owner_discord_user_id  # [authorization authority]: Must not be taken from command options.

    def accept(self, request: SubmissionRequest) -> SubmissionResult:
        """@description: Accept one share or replay its original receipt without issuing Nickel.
        @param self: Synchronous worker-owned repository boundary.
        @param request: URL and trusted evidence; the database assigns acceptance order.
        @return: Persisted original submission outcome.
        """
        require_reference(request.operation_id)
        if request.approval_source is not None or request.song_eligible is not None:
            raise ValueError("Approval must use the audited review boundary after acceptance")
        with submission_transaction(self._factory) as (cursor, control):
            return accept_submission(cursor, control, request)

    def accept_pending(self, discord_user_id: int, source_url: str, operation_id: str) -> SubmissionResult:
        """@description: Admit untrusted Discord URLs without accepting evidence or awarding Nickel.
        @param self: Existing transaction-owning submission repository.
        @param discord_user_id: Authenticated interaction submitter.
        @param source_url: User URL, never identity or eligibility evidence.
        @param operation_id: Stable server-assigned interaction retry key.
        @return: New pending receipt or the original operation receipt on replay.
        """
        request = _pending_request(discord_user_id, source_url, operation_id)
        with submission_transaction(self._factory) as (cursor, control):
            if not control["snapshot_ready"] or not control["legacy_writers_quiesced"] or control["settlement_enabled"]:
                raise PersistenceNotReady("Pending intake requires reviewed isolation and disabled settlement")
            cursor.execute("SELECT video_id,identity_json,review_json,review_revision,review_status "
                           "FROM jb2_videos WHERE video_id = %s", (request.identity.video.video_id,))
            current = cursor.fetchone()  # [authoritative identity]: A public resubmission never replaces Owner evidence.
            if current is not None:
                request = replace(request, identity=decode_current_identity(current))
            return accept_submission(cursor, control, request)

    def history(self, discord_user_id: int) -> list[dict]:
        """@description: Limit the public projection to the caller's submissions and current review states.
        @param self: Existing repository with worker-owned connections.
        @param discord_user_id: Authenticated recipient, never a slash-command option.
        @return: Bounded newest-first submission rows without another user's private evidence.
        """
        with closing(self._factory()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT e.operation_id,e.video_id,v.review_status,e.reward_status "
                    "FROM jb2_events e JOIN jb2_videos v ON v.video_id=e.video_id "
                    "WHERE e.discord_user_id=%s ORDER BY e.accepted_sequence DESC LIMIT %s",
                    (discord_user_id, HISTORY_LIMIT),
                )
                return list(cursor.fetchall())

    def snapshot(self) -> SubmissionSnapshot:
        """@description: Supply a globally covered current projection without a caller completeness switch.
        @param self: Synchronous repository boundary.
        @return: Repository-certified snapshot, possibly conservatively incomplete.
        """
        with submission_transaction(self._factory) as (cursor, control):
            return load_snapshot(cursor, control)

    def settle(self, operation_id: str) -> RewardDecision:
        """@description: Recompute eligibility and atomically settle a first-version reward.
        @param self: Synchronous repository boundary, not a public Discord award command.
        @param operation_id: Existing accepted operation or its settlement retry key.
        @return: Durable award receipt or current blocked decision.
        """
        require_reference(operation_id)
        with submission_transaction(self._factory) as (cursor, control):
            return settle_reward(cursor, control, operation_id)

    def review(self, command: ReviewCommand) -> ReviewSnapshot:
        """@description: Authorize Owner decisions before entering the database transaction.
        @param self: Repository retaining deployment Owner identity.
        @param command: Authenticated actor and version-checked audited review intent.
        @return: Original or newly persisted review result.
        """
        require_owner(command.actor_id, self._owner_id, command.reason)
        with submission_transaction(self._factory) as (cursor, control):
            return apply_review(cursor, control, command)

    def merge(self, decision_id: str, actor_id: int, reason: str, source: str, target: str) -> tuple[str, str]:
        """@description: Persist an Owner-confirmed identity merge while preserving original awards.
        @param self: Repository with configured Owner authorization.
        @param decision_id: Idempotent merge action key.
        @param actor_id: Authenticated Discord actor identity.
        @param reason: Trusted merge evidence explanation.
        @param source: Existing source version.
        @param target: Existing retained version.
        @return: Original canonical source and target references.
        """
        require_owner(actor_id, self._owner_id, reason)
        with submission_transaction(self._factory) as (cursor, control):
            return merge_versions(cursor, control, decision_id, actor_id, reason, source, target)

    def exclude(self, decision_id: str, actor_id: int, reason: str, evidence: VersionExclusion) -> VersionExclusion:
        """@description: Add audited scoped negative evidence without opening a new recording identity.
        @param self: Owner-authorized repository boundary.
        @param decision_id: Idempotent evidence action key.
        @param actor_id: Authenticated Owner identity.
        @param reason: Trusted evidence explanation.
        @param evidence: Explicit exclusion of one video from one version.
        @return: Original or newly persisted proof.
        """
        require_owner(actor_id, self._owner_id, reason)
        with submission_transaction(self._factory) as (cursor, control):
            return store_exclusion(cursor, control, decision_id, actor_id, reason, evidence)

    def pending_reminders(self, actor_id: int) -> tuple[str, ...]:
        """@description: Reserve the Owner's daily pending summary without external delivery.
        @param self: Repository with configured Owner identity.
        @param actor_id: Trusted scheduler's configured Owner target.
        @return: Stable UTC-day pending operation IDs, with no Discord send.
        """
        require_owner(actor_id, self._owner_id, "scheduled pending summary")
        with submission_transaction(self._factory) as (cursor, control):
            return pending_summary(cursor, self._owner_id)


def _pending_request(discord_user_id: int, source_url: str, operation_id: str) -> SubmissionRequest:
    """@description: Keep public intake facts separate from privileged review evidence.
    @param discord_user_id: Authenticated Discord submitter.
    @param source_url: Untrusted YouTube URL normalized by the existing parser.
    @param operation_id: Stable interaction-derived idempotency reference.
    @return: Evidence-free request whose sequence is assigned inside the database transaction.
    """
    require_reference(operation_id)
    if len(operation_id) > 128 or type(discord_user_id) is not int or discord_user_id <= 0:
        raise ValueError("Invalid public submission identity")
    video = VideoIdentity(parse_youtube_url(source_url).video_id)  # [unresolved subject]: URL identity is not recording evidence.
    return SubmissionRequest(source_url, operation_id, discord_user_id, 0,
                             VersionAssignment(video), ReviewSnapshot(video), 0)
