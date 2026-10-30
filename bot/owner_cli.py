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
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any, Callable, Mapping

from bot.config import ConfigurationError, DatabaseSettings
from bot.database.connection import open_connection
from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEligibility, DurationEvidence, assess_duration
from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.owner_review import OwnerReviewService, ReviewAction, require_traceable_source
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.version_identity import RecordingVersionIdentity

OWNER_ENV = "DISCORD_OWNER_USER_ID"  # [configured Owner]: The only source of the acting identity.
DATABASE_ENV = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")  # [explicit target]: Every value must be stated; none is defaulted.
TOKEN_TTL = timedelta(minutes=5)  # [approval preview lifetime]: Matches the Owner review service.
BASIC_VERSION_KIND = "basic"  # [unnamed version]: Maps to no VersionClaimKind, as in the Discord command.
VERSION_KIND_CHOICES = (BASIC_VERSION_KIND, *(kind.name for kind in VersionClaimKind))  # [version categories]: Closed vocabulary.
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


def version_digest(operation: str, expected_revision: int, video_id: str, version_name: str, version_kind: str, source: str,
                   reason: str, owner_id: int) -> str:
    """@description: Bind a version confirmation preview and its write to the same operation, revision, video, version claim, source, reason and Owner.
    @param operation: Operation identifier.
    @param expected_revision: Review revision the Owner examined.
    @param video_id: Video the stored operation names.
    @param version_name: Canonical recording version reference the Owner asserts.
    @param version_kind: Named category, or the basic marker.
    @param source: Traceable source of the new evidence.
    @param reason: Audit explanation.
    @param owner_id: Acting Owner identifier.
    @return: Lowercase hexadecimal SHA-256.
    """
    payload = {"kind": "version", "operation": operation, "expected_revision": expected_revision, "video": video_id,
               "version": version_name, "version_kind": version_kind, "source": source, "reason": reason, "owner": owner_id}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()


def settle_digest(operations: list, switch_enabled: bool, owner_id: int) -> str:
    """@description: Bind a named settlement window to the exact operation list and the switch value it was previewed against.
    @param operations: Operation identifiers, order-insensitive.
    @param switch_enabled: Settlement switch value read at preview or write time.
    @param owner_id: Acting Owner identifier.
    @return: Lowercase hexadecimal SHA-256.
    """
    payload = {"kind": "settle", "operations": sorted(operations), "switch": switch_enabled, "owner": owner_id}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()


def switch_digest(enabled: bool, current: bool, owner_id: int) -> str:
    """@description: Bind a settlement mode change to the target and the value it was previewed against.
    @param enabled: Target switch value.
    @param current: Switch value read at preview or write time.
    @param owner_id: Acting Owner identifier.
    @return: Lowercase hexadecimal SHA-256.
    """
    payload = {"kind": "settlement-switch", "enabled": enabled, "current": current, "owner": owner_id}
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
    @param: none
    @return: Configured argument parser.
    """
    parser = argparse.ArgumentParser(prog="owner_cli", description="Owner duration evidence and approval (operator).")
    commands = parser.add_subparsers(dest="command")
    _add_duration_commands(commands)
    _add_approval_commands(commands)
    _add_version_commands(commands)
    _add_settlement_commands(commands)
    _add_switch_commands(commands)
    return parser


def _add_duration_commands(commands: Any) -> None:
    """@description: Register the duration evidence preview and write operations with their evidence flags.
    @param commands: Top-level subparser collection that receives the duration command.
    @return: None.
    """
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


def _add_approval_commands(commands: Any) -> None:
    """@description: Register the Owner approval preview and write operations with their content judgement flag.
    @param commands: Top-level subparser collection that receives the approve command.
    @return: None.
    """
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


def _add_version_commands(commands: Any) -> None:
    """@description: Register the recording version preview and write operations, kept separate from approval.
    @param commands: Top-level subparser collection that receives the version command.
    @return: None.
    """
    version = commands.add_parser("version", help="Owner-confirmed recording version, separate from approval")
    version_actions = version.add_subparsers(dest="action", required=True)
    for name in ("preview", "write"):
        action = version_actions.add_parser(name)
        action.add_argument("--operation", required=True)
        action.add_argument("--expected-revision", type=int, required=True)
        action.add_argument("--version-name", required=True)
        action.add_argument("--version-kind", choices=VERSION_KIND_CHOICES, required=True)
        action.add_argument("--source", required=True)
        action.add_argument("--reason", required=True)
    version_actions.choices["write"].add_argument("--digest", required=True)


def _add_settlement_commands(commands: Any) -> None:
    """@description: Register the named settlement window preview and write operations over the listed operations.
    @param commands: Top-level subparser collection that receives the settle command.
    @return: None.
    """
    settlement = commands.add_parser("settle", help="Named settlement window: switch on, settle the listed operations, switch restored")
    settle_actions = settlement.add_subparsers(dest="action", required=True)
    for name in ("preview", "write"):
        settle_actions.add_parser(name).add_argument("--operation", action="append", required=True)
    settle_actions.choices["write"].add_argument("--digest", required=True)


def _add_switch_commands(commands: Any) -> None:
    """@description: Register the explicit database switch used when entering or leaving automatic settlement mode.
    @param commands: Top-level subparser collection that receives the settlement-switch command.
    @return: None.
    """
    switch = commands.add_parser("settlement-switch", help="Set the database settlement switch for automatic settlement mode")
    switch_actions = switch.add_subparsers(dest="action", required=True)
    for name in ("preview", "write"):
        switch_actions.add_parser(name).add_argument("--enabled", choices=("on", "off"), required=True)
    switch_actions.choices["write"].add_argument("--digest", required=True)


def _settlement_switch(args: argparse.Namespace, repository: Any, owner: int) -> int:
    """@description: Preview or write the settlement switch, then read it back; no reward is settled by this command.
    @param args: Parsed arguments of the settlement-switch subcommand.
    @param repository: Repository the operator reads and writes through.
    @param owner: Acting Owner identifier.
    @return: Process exit code.
    """
    enabled = args.enabled == "on"
    current = repository.settlement_enabled(owner)
    digest = switch_digest(enabled, current, owner)
    if args.action == "preview":
        _report([f"settlement switch now: {current}", f"target: {enabled}", f"digest: {digest}", "writes: none (preview only)"])
        return 0
    if digest != args.digest:
        raise OperatorRefused("CONFIRMATION_DIGEST_MISMATCH")
    repository.set_settlement_enabled(owner, enabled)
    stored = repository.settlement_enabled(owner)
    _report([f"settlement switch now: {stored}"])
    return 0 if stored is enabled else 1


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


def _version(args: argparse.Namespace, repository: Any, owner: int, clock: Callable[[], datetime]) -> int:
    """@description: Preview or write one Owner-confirmed recording version through the existing version confirmation of the owner review service.
    @param args: Parsed arguments of the version subcommand.
    @param repository: Repository the operator reads and writes through.
    @param owner: Acting Owner identifier.
    @param clock: Clock for the evidence time and the preview lifetime.
    @return: Process exit code.
    """
    try:
        require_traceable_source(args.source)
    except ValueError:
        raise OperatorRefused("SOURCE_NOT_TRACEABLE") from None
    projection = repository.review_view(owner, args.operation)
    video = projection.review.video
    digest = version_digest(args.operation, args.expected_revision, video.video_id, args.version_name, args.version_kind, args.source,
                            args.reason, owner)
    if args.action == "write" and digest != args.digest:
        raise OperatorRefused("CONFIRMATION_DIGEST_MISMATCH")
    kind = None if args.version_kind == BASIC_VERSION_KIND else VersionClaimKind[args.version_kind]
    evidence = record_evidence(uuid.uuid4().hex, video, EvidenceSourceType.OWNER_REVIEW, args.source, clock(), args.reason, owner,
                               RecordingVersionIdentity(args.version_name), version_kind=kind)
    service = OwnerReviewService(repository, owner, clock, token_ttl=TOKEN_TTL)
    preview = service.preview_version_confirmation(owner, args.operation, evidence, args.reason, args.expected_revision)
    if args.action == "preview":
        _report([f"operation: {args.operation}", f"video: {video.video_id}", f"current revision: {args.expected_revision}",
                 f"projected identity: {preview.projected_identity.status.value}", f"digest: {digest}", "writes: none (preview only)"])
        return 0
    review = service.confirm(owner, preview.token_id)
    _report([f"operation: {args.operation}", f"review status: {review.status.value}", f"revision: {review.version}"])
    return 0


def _settle_window(repository: Any, owner: int, operations: list) -> int:
    """@description: Open the settlement switch only around the named settle calls, then restore the value read before the window; a failed restore is reported on its own line.
    @param repository: Repository the operator reads and writes through.
    @param owner: Acting Owner identifier.
    @param operations: Distinct operation identifiers to settle in order.
    @return: Process exit code: 0 only when every call succeeded and the switch was restored.
    """
    results, failure = [], None
    try:
        repository.set_settlement_enabled(owner, True)  # [window open]: Only for the named operations below.
        for operation in operations:
            results.append(f"{operation}: {repository.settle(operation).status.value}")
    except Exception as error:  # [class name only]: Messages can echo record contents.
        failure = type(error).__name__
    try:
        repository.set_settlement_enabled(owner, False)  # [restore]: Runs on success and on failure alike.
        restored = repository.settlement_enabled(owner)  # [read back]: A separate read confirms what the database now holds.
    except Exception as restore_error:
        _report(results + [f"failed: {failure or 'none'}",
                           f"settlement switch RESTORE FAILED ({type(restore_error).__name__}): it may still be enabled; stop and check it before anything else"])
        return 1
    _report(results + ([f"failed: {failure}"] if failure else []) + [f"settlement switch restored: {restored}"])
    if restored is not False:
        _report(["settlement switch RESTORE MISMATCH: read back enabled; stop and check it before anything else"])
        return 1
    return 1 if failure else 0


def _settle(args: argparse.Namespace, repository: Any, owner: int) -> int:
    """@description: Preview or run one named settlement window over an explicit operation list, refusing unless the switch is off before the window.
    @param args: Parsed arguments of the settle subcommand.
    @param repository: Repository the operator reads and writes through.
    @param owner: Acting Owner identifier.
    @return: Process exit code.
    """
    operations = list(dict.fromkeys(args.operation))
    if not operations or len(operations) != len(args.operation):
        raise OperatorRefused("SETTLEMENT_OPERATIONS_INVALID")
    switch_enabled = repository.settlement_enabled(owner)
    digest = settle_digest(operations, switch_enabled, owner)
    if args.action == "preview":
        _report([f"operations: {', '.join(operations)}", f"settlement switch now: {switch_enabled}", f"digest: {digest}",
                 "writes: none (preview only)"])
        return 0
    if digest != args.digest:
        raise OperatorRefused("CONFIRMATION_DIGEST_MISMATCH")
    if switch_enabled is not False:
        raise OperatorRefused("SETTLEMENT_SWITCH_MUST_BE_OFF_BEFORE_WINDOW")
    return _settle_window(repository, owner, operations)


def _dispatch(args: argparse.Namespace, repository: Any, owner: int, now: Callable[[], datetime]) -> int:
    """@description: Route one parsed subcommand to its handler.
    @param args: Parsed arguments.
    @param repository: Repository the operator reads and writes through.
    @param owner: Acting Owner identifier.
    @param now: Timezone-aware clock.
    @return: Process exit code.
    """
    if args.command == "duration":
        return _duration(args, repository, owner)
    if args.command == "approve":
        return _approval(args, repository, owner, now)
    if args.command == "version":
        return _version(args, repository, owner, now)
    if args.command == "settlement-switch":
        return _settlement_switch(args, repository, owner)
    return _settle(args, repository, owner)


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
        return _dispatch(args, repository, owner, now)
    except OperatorRefused as refusal:
        _report([f"refused: {refusal}"])
    except ConfigurationError:
        _report(["refused: CONFIGURATION_ERROR"])
    except Exception as failure:  # [class name only]: Messages can echo record contents, so only the class is printed.
        _report([f"failed: {type(failure).__name__}"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
