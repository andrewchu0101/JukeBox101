"""Operator command line for the Owner's duration evidence and approval, reusing the existing Owner review service and repository.

Nothing runs by default: without a subcommand the program reads no setting, opens no connection and writes nothing. The acting Owner comes only from the
configured DISCORD_OWNER_USER_ID, never from an argument. The database target is the explicitly configured one; a missing DB_PORT refuses, so no
default-port path exists. A preview reads and reports; a write requires its digest, which binds the operation, the expected revision, the video the
stored operation names, the inputs and the decision identifier. Refusals name only the reason code and never print a secret.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any, Callable, Mapping

from bot.config import ConfigurationError, DatabaseSettings
from bot.database.connection import open_connection
from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEligibility, DurationEvidence, assess_duration
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement

OWNER_ENV = "DISCORD_OWNER_USER_ID"  # [configured Owner]: The only source of the acting identity.
DATABASE_ENV = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")  # [explicit target]: Every value must be stated; none is defaulted.
TOKEN_TTL = timedelta(minutes=5)  # [approval preview lifetime]: Matches the Owner review service.
APPROVAL_LABEL = "approve"  # [approval decision label]: Fixed for the digest; approvals take their decision identifier from the service.
VERDICT_TEXT = {DurationEligibility.PASS: "PASS", DurationEligibility.REJECT: "REJECT", DurationEligibility.PENDING_VALIDATION: "PENDING_VALIDATION"}


class OperatorRefused(RuntimeError):
    """Refuse an operator step before any write; the message names the reason code only."""


def owner_identity(environ: Mapping[str, str]) -> int:
    """@description: Read the configured Owner identity, refusing a missing or non-positive value.
    @param environ: Environment mapping to read.
    @return: Positive Owner identifier.
    """
    raw = environ.get(OWNER_ENV)
    if raw is None or not raw.isdecimal() or int(raw) <= 0:
        raise OperatorRefused("OWNER_IDENTITY_MISSING_OR_INVALID")
    return int(raw)


def operator_settings(environ: Mapping[str, str]) -> DatabaseSettings:
    """@description: Build the explicitly configured database destination; every value must be present and the application account must not be an administrator.
    @param environ: Environment mapping to read.
    @return: Database settings; no default host, port, schema or account is supplied.
    """
    missing = [name for name in DATABASE_ENV if not environ.get(name)]
    if missing:
        raise OperatorRefused("DATABASE_SETTING_MISSING:" + ",".join(missing))  # [names only]: Values are never printed.
    if not environ["DB_PORT"].isdecimal() or not 0 < int(environ["DB_PORT"]) < 65536:
        raise OperatorRefused("DATABASE_PORT_INVALID")
    if environ["DB_USER"].casefold() == "root":
        raise OperatorRefused("DATABASE_ACCOUNT_IS_ADMINISTRATIVE")
    return DatabaseSettings(environ["DB_HOST"], int(environ["DB_PORT"]), environ["DB_USER"], environ["DB_PASSWORD"], environ["DB_NAME"])


def duration_digest(operation: str, expected_revision: int, video_id: str, seconds: int | None, source: str, decision_id: str,
                    reason: str, owner_id: int, *, approve: bool = False) -> str:
    """@description: Bind a confirmation to the exact operation, revision, video, inputs, decision identifier, reason and acting Owner.
    @param operation: Operation identifier.
    @param expected_revision: Review revision the Owner examined.
    @param video_id: Video the stored operation names.
    @param seconds: Owner-stated seconds, or None for an approval.
    @param source: Owner-stated source, or the evidence reference for an approval.
    @param decision_id: Idempotency key of the decision.
    @param reason: Audit explanation.
    @param owner_id: Acting Owner identifier.
    @param approve: Whether the digest belongs to an approval.
    @return: Lowercase hexadecimal SHA-256.
    """
    payload = {"kind": "approve" if approve else "duration", "operation": operation, "expected_revision": expected_revision,
               "video": video_id, "seconds": seconds, "source": source, "decision_id": decision_id, "reason": reason, "owner": owner_id}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()


def build_duration(video_id: str, seconds: int, source: str, full_video_confirmed: bool, reliable_confirmed: bool) -> DurationEvidence:
    """@description: Turn the Owner's explicit statements into duration evidence; reliability and full-video status are never assumed.
    @param video_id: Video the stored operation names.
    @param seconds: Owner-stated full-video seconds.
    @param source: Owner-stated, non-empty source of the observation.
    @param full_video_confirmed: Explicit statement that the whole video was observed.
    @param reliable_confirmed: Explicit statement that the Owner stands behind the duration.
    @return: Duration evidence bound to the video.
    """
    if full_video_confirmed is not True or reliable_confirmed is not True:
        raise OperatorRefused("OWNER_MUST_STATE_FULL_VIDEO_AND_RELIABILITY")
    if type(seconds) is not int or not source.strip():
        raise OperatorRefused("DURATION_INPUT_INVALID")
    return DurationEvidence(VideoIdentity(video_id), seconds, source.strip(), True, True)


def _parser() -> argparse.ArgumentParser:
    """@description: Declare the operator subcommands; no actor, video or database argument exists.
    @return: Configured argument parser.
    """
    parser = argparse.ArgumentParser(prog="owner_cli", description="Owner duration evidence and approval (operator).")
    commands = parser.add_subparsers(dest="command")
    duration = commands.add_parser("duration", help="Owner-stated full-video duration evidence")
    duration_actions = duration.add_subparsers(dest="action", required=True)
    for name in ("preview", "write"):
        action = duration_actions.add_parser(name)
        action.add_argument("--operation", required=True)
        action.add_argument("--seconds", type=int, required=True)
        action.add_argument("--source", required=True)
        action.add_argument("--decision-id", required=True)
        action.add_argument("--reason", required=True)
        action.add_argument("--full-video-confirmed", action="store_true")
        action.add_argument("--reliable-confirmed", action="store_true")
    duration_actions.choices["write"].add_argument("--expected-revision", type=int, required=True)
    duration_actions.choices["write"].add_argument("--digest", required=True)
    approval = commands.add_parser("approve", help="Owner approval with a content judgement")
    approval_actions = approval.add_subparsers(dest="action", required=True)
    for name in ("preview", "write"):
        action = approval_actions.add_parser(name)
        action.add_argument("--operation", required=True)
        action.add_argument("--expected-revision", type=int, required=True)
        action.add_argument("--reason", required=True)
        action.add_argument("--evidence-reference", required=True)
        action.add_argument("--is-song-confirmed", action="store_true")
    approval_actions.choices["write"].add_argument("--digest", required=True)
    return parser


def _report(lines: list) -> None:
    """@description: Print the operator-facing lines; only identifiers, verdicts and digests are ever printed.
    @param lines: Text lines in order.
    @return: None.
    """
    for line in lines:
        print(line)


def _duration(args: argparse.Namespace, repository: Any, owner: int) -> int:
    """@description: Preview or write one Owner duration decision for the stored operation's own video.
    @param args: Parsed arguments of the duration subcommand.
    @param repository: Repository the operator reads and writes through.
    @param owner: Acting Owner identifier.
    @return: Process exit code.
    """
    projection = repository.review_view(owner, args.operation)
    video_id = projection.review.video.video_id
    evidence = build_duration(video_id, args.seconds, args.source, args.full_video_confirmed, args.reliable_confirmed)
    verdict = assess_duration(evidence)
    if args.action == "preview":
        digest = duration_digest(args.operation, projection.review.version, video_id, args.seconds, evidence.source, args.decision_id,
                                 args.reason, owner)
        lines = [f"operation: {args.operation}", f"video: {video_id}", f"current revision: {projection.review.version}",
                 f"seconds: {args.seconds}", f"verdict: {VERDICT_TEXT[verdict]}", f"digest: {digest}",
                 "writes: none (preview only)"]
        if verdict is DurationEligibility.REJECT:
            lines.append("警告：此時長超出 60–720 秒；確認寫入後此影片將被最終拒絕，本工具無法撤回。")  # [explicit consequence]: Shown before any confirmation.
        _report(lines)
        return 0
    digest = duration_digest(args.operation, args.expected_revision, video_id, args.seconds, evidence.source, args.decision_id,
                             args.reason, owner)
    if digest != args.digest:
        raise OperatorRefused("CONFIRMATION_DIGEST_MISMATCH")
    review = repository.review(ReviewCommand(args.decision_id, args.operation, owner, args.reason, args.expected_revision, duration=evidence))
    _report([f"operation: {args.operation}", f"review status: {review.status.value}", f"revision: {review.version}"])
    return 0


def _approval_digest(args: argparse.Namespace, video_id: str, owner: int) -> str:
    """@description: Bind an approval preview and its write to the same arguments, so a changed input cannot reuse an earlier digest.
    @param args: Parsed arguments of the approve subcommand.
    @param video_id: Video the stored operation describes.
    @param owner: Acting Owner identifier.
    @return: Digest the write must present.
    """
    return duration_digest(args.operation, args.expected_revision, video_id, None, args.evidence_reference, APPROVAL_LABEL,
                           args.reason, owner, approve=True)


def _approval(args: argparse.Namespace, repository: Any, owner: int, clock: Callable[[], datetime]) -> int:
    """@description: Preview or write one Owner approval through the existing preview and confirmation of the owner review service.
    @param args: Parsed arguments of the approve subcommand.
    @param repository: Repository the operator reads and writes through.
    @param owner: Acting Owner identifier.
    @param clock: Clock for the content judgement and the preview lifetime.
    @return: Process exit code.
    """
    if args.is_song_confirmed is not True:
        raise OperatorRefused("OWNER_MUST_STATE_THE_SONG_JUDGEMENT")
    video_id = repository.review_view(owner, args.operation).review.video.video_id
    judgement = ContentJudgement(VideoIdentity(video_id), owner, args.reason, clock(), args.evidence_reference, True)
    service = OwnerReviewService(repository, owner, clock, token_ttl=TOKEN_TTL)
    if args.action == "preview":
        preview = service.preview(owner, args.operation, ReviewAction.APPROVE, args.reason, args.expected_revision,
                                  args.evidence_reference, content=judgement)
        digest = _approval_digest(args, video_id, owner)
        _report([f"operation: {args.operation}", f"video: {video_id}", f"current revision: {args.expected_revision}",
                 f"projected status: {preview.projected_status.value}", f"digest: {digest}", "writes: none (preview only)"])
        return 0
    digest = _approval_digest(args, video_id, owner)
    if digest != args.digest:
        raise OperatorRefused("CONFIRMATION_DIGEST_MISMATCH")
    token = service.preview(owner, args.operation, ReviewAction.APPROVE, args.reason, args.expected_revision,
                            args.evidence_reference, content=judgement).token_id
    review = service.confirm(owner, token)
    _report([f"operation: {args.operation}", f"review status: {review.status.value}", f"revision: {review.version}"])
    return 0


def main(argv: list | None = None, *, environ: Mapping[str, str] | None = None,
         repository_factory: Callable[[DatabaseSettings, int], Any] | None = None,
         clock: Callable[[], datetime] | None = None) -> int:
    """@description: Run one operator subcommand; with no subcommand nothing is read, connected or written.
    @param argv: Arguments after the program name, or None for the process arguments.
    @param environ: Environment mapping, or None for the process environment.
    @param repository_factory: Builds the repository from validated settings and the Owner; None uses the production connection.
    @param clock: Timezone-aware clock, or None for the current UTC time.
    @return: Process exit code: 0 on success, 1 on any refusal or failure.
    """
    args = _parser().parse_args(argv)
    if args.command is None:
        _parser().print_usage()
        return 0
    settings_source = os.environ if environ is None else environ
    now = clock if clock is not None else (lambda: datetime.now(timezone.utc))
    try:
        owner = owner_identity(settings_source)
        settings = operator_settings(settings_source)  # [validated before any connection]: Refusals happen before the factory is called.
        factory = repository_factory or (lambda s, o: SubmissionRepository(partial(open_connection, s), o))
        repository = factory(settings, owner)
        if args.command == "duration":
            return _duration(args, repository, owner)
        return _approval(args, repository, owner, now)
    except OperatorRefused as refusal:
        _report([f"refused: {refusal}"])
    except ConfigurationError:
        _report(["refused: CONFIGURATION_ERROR"])
    except Exception as failure:  # [class name only]: Messages can echo record contents, so only the class is printed.
        _report([f"failed: {type(failure).__name__}"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
