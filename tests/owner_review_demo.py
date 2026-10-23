"""Run an offline, synthetic-data Owner review session over the real review service and the in-memory SQL fake.

Owners type commands to inspect, preview, confirm and fault-inject decisions; no MySQL, Discord or Gateway connection is ever opened.
"""

from tests.intake_fixture import seed_intake
from datetime import datetime, timedelta, timezone
from typing import Callable

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceRecord, EvidenceSourceType, VersionClaimKind, record_evidence
from bot.owner_review import (
    ExpiredReviewToken, OwnerReviewService, ReviewAction, ReviewOutcomeUnknown, UnknownReviewToken,
)
from bot.review import ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777  # [configured Owner]: Synthetic stand-in for the deployment-configured Owner Discord identity.
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)  # [synthetic start]: Fixed instant so output is reproducible.
TOKEN_TTL = timedelta(minutes=5)  # [confirmation window]: Matches the service default used by the demo.
OPERATIONS = ("op-1", "op-2", "op-3", "op-4", "op-5")  # [seeded subjects]: Five synthetic submissions.
SEEDED_SECONDS = {4: 30}  # [duration seeds]: Operation 4 is deliberately too short to approve.
DEFAULT_SECONDS = 180  # [valid duration]: Inside the inclusive hard limits.
REFUSALS = (PermissionError, ValueError, ExpiredReviewToken, UnknownReviewToken, ReviewOutcomeUnknown)
BANNER = (
    "JukeBox101 Owner 審查示範（離線）",
    "所有資料皆為合成資料；本示範不連線 MySQL、Discord 或 Gateway。",
    "輸入 help 查看指令，quit 離開。",
)



def _synthetic_song_judgement(video, judged_by: int, operation_id: str, retrieved_at):
    """@description: Build the synthetic Owner song judgement that approval must present for this video.
    @param video: Video the judgement describes; the gate refuses any other video.
    @param judged_by: Acting Owner identity recorded as the judge.
    @param operation_id: Operation whose citation this judgement belongs to.
    @param retrieved_at: Demo clock time of the judgement.
    @return: Synthetic positive song judgement for approval previews.
    """
    return ContentJudgement(video, judged_by, "synthetic song judgement", retrieved_at,
                            f"demo-content-{operation_id}", True)


class SyntheticClock:
    """@description: Provide a controllable synthetic clock so token expiry can be demonstrated.
    @param: none
    @return: Callable clock with an advance method.
    """

    def __init__(self) -> None:
        """@description: Start at a fixed synthetic instant.
        @param self: Mutable synthetic clock.
        @return: None.
        """
        self.current = START  # [synthetic now]: Moved only by the tick command.

    def __call__(self) -> datetime:
        """@description: Return the synthetic current instant.
        @param self: Synthetic clock.
        @return: Current timezone-aware instant.
        """
        return self.current

    def advance(self, seconds: int) -> None:
        """@description: Move synthetic time forward for expiry checks.
        @param self: Synthetic clock.
        @param seconds: Elapsed seconds.
        @return: None.
        """
        self.current += timedelta(seconds=seconds)


def _video(number: int) -> VideoIdentity:
    """@description: Build the synthetic YouTube-shaped identity for one seeded operation.
    @param number: Stable synthetic number.
    @return: Case-sensitive synthetic video identity.
    """
    return VideoIdentity(f"Synth{number:04d}X{number}")


def _submission(number: int, seconds: int) -> SubmissionRequest:
    """@description: Create one synthetic accepted submission with full-video duration evidence.
    @param number: Stable synthetic number used for the video and operation identifiers.
    @param seconds: Reliable full-video duration in seconds.
    @return: Intake request with no approval yet.
    """
    video = _video(number)
    duration = DurationEvidence(video, seconds, "synthetic-duration", True, True)
    return SubmissionRequest(f"https://youtu.be/{video.video_id}", f"op-{number}", 100 + number, 999,
                             VersionAssignment(video), ReviewSnapshot(video), 0, duration)


def _describe_action(action: ReviewAction) -> str:
    """@description: Map the Owner's typed action word to the service's review action.
    @param action: Lower-case word typed after the operation identifier.
    @return: Matching ReviewAction.
    """
    if action == "approve":
        return ReviewAction.APPROVE
    if action == "reject":
        return ReviewAction.REJECT
    raise ValueError("動作必須是 approve 或 reject")


class DemoSession:
    """@description: Hold one synthetic database, real repository, review service and acting identity for one demonstration.
    @param: none
    @return: Session that turns typed commands into printed output lines.
    """

    def __init__(self) -> None:
        """@description: Build and seed a fresh synthetic world with only in-memory state.
        @param self: Demo session.
        @return: None.
        """
        self.database = FakeDatabase()  # [offline SQL fake]: No socket, no file, no wall clock.
        self.database.now = START.replace(tzinfo=None)  # [fake UTC clock]: Matches the fake's naive database clock.
        self.repository = SubmissionRepository(self.database.connect, OWNER_ID)  # [real repository]: Uses the fake connection only.
        self.clock = SyntheticClock()  # [demo time]: Advanced only by the tick command.
        self.service = OwnerReviewService(self.repository, OWNER_ID, self.clock, token_ttl=TOKEN_TTL)
        self.acting_id = OWNER_ID  # [acting Discord identity]: Defaults to the Owner; changed only by the as command.
        self.finished = False  # [session state]: Set by quit so the loop stops.
        self.evidence_sequence = 0  # [demo evidence counter]: Keeps synthetic evidence identifiers unique.
        self.commands = self._build_commands()
        self._seed()

    def execute(self, line: str) -> list[str]:
        """@description: Dispatch one typed command and print refusals as lines rather than crashing the session.
        @param self: Demo session.
        @param line: Raw command text typed by the Owner.
        @return: Output lines for this command.
        """
        words = line.split()
        if not words:
            return []
        handler = self.commands.get(words[0])
        if handler is None:
            return [f"未知指令：{words[0]}（輸入 help 查看指令）"]
        try:
            return handler(words[1:])
        except REFUSALS as error:
            return [f"拒絕 [{type(error).__name__}]: {error}"]

    def _build_commands(self) -> dict[str, Callable[[list[str]], list[str]]]:
        """@description: Register every typed command in one place so help and dispatch cannot drift apart.
        @param self: Demo session.
        @return: Command word mapped to its handler.
        """
        return {"list": self._list, "show": self._show, "as": self._as, "preview": self._preview,
                "confirm": self._confirm, "retry": self._retry, "fault": self._fault, "tick": self._tick,
                "evidence": self._evidence, "help": self._help, "quit": self._quit}

    def _seed(self) -> None:
        """@description: Create the five synthetic operations and record their identities through the real review path.
        @param self: Demo session.
        @return: None.
        """
        for number in range(1, 6):
            seed_intake(self.repository, _submission(number, SEEDED_SECONDS.get(number, DEFAULT_SECONDS)))
        self._seed_identity(2, [self._record("seed-ev-2", 2, "demo-version-A")])
        self._seed_identity(3, [self._record("seed-ev-3a", 3, "demo-version-A"),
                                self._record("seed-ev-3b", 3, "demo-version-B", hours=1)])
        self._seed_evidence(5, [self._record("seed-ev-5", 5, "demo-version-A", source=EvidenceSourceType.PLATFORM_METADATA)])

    def _seed_evidence(self, number: int, records: list[EvidenceRecord]) -> None:
        """@description: Record audited evidence through the re-examination path, which never changes the decision.
        @param self: Demo session.
        @param number: Synthetic operation number.
        @param records: Evidence facts to append for that operation.
        @return: None.
        """
        for record in records:
            self.service.add_reexamination_evidence(OWNER_ID, f"op-{number}", record, "synthetic inspection",
                                                    f"seed-reexam-{record.evidence_id}")

    def _seed_identity(self, number: int, records: list[EvidenceRecord]) -> None:
        """@description: Record evidence, then persist the resolved identity through the same review command the Owner path uses.
        @param self: Demo session.
        @param number: Synthetic operation number.
        @param records: Audited evidence facts for that video.
        @return: None.
        """
        operation_id = f"op-{number}"
        self._seed_evidence(number, records)
        assignment = resolve_video_version(_video(number), records)
        revision = self.service.view(OWNER_ID, operation_id).review.version
        self.repository.review(ReviewCommand(f"seed-identity-{number}", operation_id, OWNER_ID, "synthetic inspection",
                                             revision, identity=assignment))

    def _record(self, evidence_id: str, number: int, version_name: str, hours: int = 0,
                source: EvidenceSourceType = EvidenceSourceType.OWNER_REVIEW) -> EvidenceRecord:
        """@description: Create one synthetic audited evidence fact about a seeded video.
        @param self: Demo session.
        @param evidence_id: Stable synthetic evidence identifier.
        @param number: Synthetic operation number whose video is described.
        @param version_name: Asserted synthetic recording version name.
        @param hours: Retrieval offset so contradictions have a deterministic order.
        @param source: Evidence category; platform evidence never confirms identity by itself.
        @return: Immutable evidence record.
        """
        return record_evidence(evidence_id, _video(number), source, f"synthetic-{evidence_id}",
                               START + timedelta(hours=hours), "synthetic inspection", OWNER_ID,
                               RecordingVersionIdentity(version_name), owner_confirmed=True,
                               version_kind=VersionClaimKind.SAME_RECORDING_AUDIO_MV)

    def _list(self, _arguments: list[str]) -> list[str]:
        """@description: Summarize every operation so the Owner can choose one to inspect.
        @param self: Demo session.
        @param _arguments: Unused.
        @return: One row per operation with status, identity, revision and evidence count.
        """
        rows = []  # [operation summary]: Read live from the service for the acting identity.
        for operation_id in OPERATIONS:
            view = self.service.view(self.acting_id, operation_id)
            rows.append(f"{operation_id} status={view.review.status.value} identity={view.identity.status.name} "
                        f"revision={view.review.version} evidence={len(view.evidence)}")
        return rows

    def _show(self, arguments: list[str]) -> list[str]:
        """@description: Print one operation's review, identity, evidence and history without changing state.
        @param self: Demo session.
        @param arguments: Operation identifier.
        @return: Detail lines for that operation.
        """
        view = self.service.view(self.acting_id, arguments[0])
        identity = view.identity
        lines = [f"審查狀態 status={view.review.status.value} revision={view.review.version}",
                 f"身分 identity={identity.status.name} version={_version_name(identity)}"]
        if identity.status is VersionIdentityStatus.CONFLICT:
            lines.append(f"衝突：同一影片有互相矛盾的可靠版本證據（最新引用 {identity.evidence_reference}）")
        lines.extend(_evidence_line(item) for item in view.evidence)
        lines.extend(_history_line(snapshot) for snapshot in view.history)
        return lines

    def _as(self, arguments: list[str]) -> list[str]:
        """@description: Switch the acting Discord identity used by later commands.
        @param self: Demo session.
        @param arguments: One numeric Discord identifier.
        @return: Confirmation of the new acting identity.
        """
        self.acting_id = int(arguments[0])
        role = "Owner" if self.acting_id == OWNER_ID else "非 Owner（審查指令將被拒絕）"
        return [f"目前身分 {self.acting_id}：{role}"]

    def _preview(self, arguments: list[str]) -> list[str]:
        """@description: Show the projected decision and its financial effect without writing anything.
        @param self: Demo session.
        @param arguments: Operation identifier, approve or reject, evidence citation, then the reason text.
        @return: Token, status transition, financial effect and expiry lines.
        """
        operation_id, action_word, citation = arguments[0], arguments[1], arguments[2]
        reason = " ".join(arguments[3:])
        view = self.service.view(self.acting_id, operation_id)
        action = _describe_action(action_word)
        content = _synthetic_song_judgement(view.review.video, self.acting_id, operation_id, self.clock()) \
            if action is ReviewAction.APPROVE else None  # [Owner song judgement]: Approval needs content, reject does not.
        preview = self.service.preview(self.acting_id, operation_id, action, reason, view.review.version, citation,
                                       content=content)
        return [f"權杖 token={preview.token_id}",
                f"預覽基準 revision={view.review.version}",
                f"狀態 {preview.current_status.value} → {preview.projected_status.value}",
                f"財務效果：{preview.financial_effect}",
                f"有效至 expires_at={preview.expires_at.isoformat()}"]

    def _confirm(self, arguments: list[str]) -> list[str]:
        """@description: Execute one preview token and print the resulting review snapshot.
        @param self: Demo session.
        @param arguments: Token identifier shown by preview.
        @return: Resulting status and revision.
        """
        return _result_lines("已執行", self.service.confirm(self.acting_id, arguments[0]))

    def _retry(self, arguments: list[str]) -> list[str]:
        """@description: Repeat a confirmation after an unknown outcome, reusing the same decision key.
        @param self: Demo session.
        @param arguments: Token identifier from the original preview.
        @return: Resulting status and revision.
        """
        return _result_lines("重試結果（同一 decision_id）", self.service.confirm(self.acting_id, arguments[0]))

    def _fault(self, arguments: list[str]) -> list[str]:
        """@description: Arm one simulated commit whose write lands but whose acknowledgement is lost.
        @param self: Demo session.
        @param arguments: Must be the single word next.
        @return: Explanation of the armed fault.
        """
        if arguments != ["next"]:
            raise ValueError("用法：fault next")
        self.database.commit_fault = "after"  # [injected ambiguity]: The fake clears this after one commit.
        return ["已設定：下一次提交會寫入但不回應（使用一次後自動清除）。"]

    def _tick(self, arguments: list[str]) -> list[str]:
        """@description: Move synthetic time forward so token expiry can be shown.
        @param self: Demo session.
        @param arguments: Seconds to advance, nonnegative.
        @return: New synthetic time.
        """
        seconds = int(arguments[0])
        if seconds < 0:
            raise ValueError("秒數不可為負數")
        self.clock.advance(seconds)
        self.database.now += timedelta(seconds=seconds)
        return [f"時鐘前進 {seconds} 秒，現在 {self.clock.current.isoformat()}"]

    def _evidence(self, arguments: list[str]) -> list[str]:
        """@description: Record re-examination evidence and show that the decision, identity and order stayed put.
        @param self: Demo session.
        @param arguments: Operation, owner or platform, version or dash, then reason text.
        @return: Evidence identifier and before/after comparison lines.
        """
        operation_id, kind, version_word, reason = arguments[0], arguments[1], arguments[2], " ".join(arguments[3:])
        source = _evidence_source(kind)
        before = self.service.view(self.acting_id, operation_id)
        self.evidence_sequence += 1
        version = None if version_word == "-" else RecordingVersionIdentity(version_word)
        record = record_evidence(f"demo-ev-{self.evidence_sequence}", before.review.video, source,
                                 f"synthetic-{kind}-{self.evidence_sequence}", self.clock(), reason, self.acting_id, version)
        self.service.add_reexamination_evidence(self.acting_id, operation_id, record, reason,
                                                f"demo-reexam-{self.evidence_sequence}")
        after = self.service.view(self.acting_id, operation_id)
        return [f"證據已記錄 id={record.evidence_id} source={source.value}", *_comparison_lines(before, after)]

    def _help(self, _arguments: list[str]) -> list[str]:
        """@description: List the supported commands in the order an Owner normally uses them.
        @param self: Demo session.
        @param _arguments: Unused.
        @return: Help lines.
        """
        return ["list                         所有合成操作的狀態、身分、revision、證據數",
                "show <op>                    單一操作的審查、身分、證據與歷史",
                "as <discord_id>              切換操作身分（預設 Owner）",
                "preview <op> approve|reject <證據引用> <理由>   產生權杖，不寫入資料",
                "confirm <token>              執行權杖",
                "retry <token>                以相同 decision_id 重試（未知結果後使用）",
                "fault next                   讓下一次提交變成「寫入但未回應」（請於 preview 之後、confirm 之前設定）",
                "tick <秒數>                  推進合成時鐘（權杖約 5 分鐘後過期）",
                "evidence <op> owner|platform <版本或 -> <理由>   新增再審查證據",
                "help                         顯示此說明",
                "quit                         離開示範"]

    def _quit(self, _arguments: list[str]) -> list[str]:
        """@description: End the session so later input is not executed.
        @param self: Demo session.
        @param _arguments: Unused.
        @return: Farewell line.
        """
        self.finished = True
        return ["再見。"]


def _version_name(identity: VersionAssignment) -> str:
    """@description: Render the confirmed version name for display, or a dash when none is asserted.
    @param identity: Current identity assignment.
    @return: Version name or "-".
    """
    return identity.version.value if identity.version is not None else "-"


def _evidence_line(record: EvidenceRecord) -> str:
    """@description: Show every audited field of one evidence fact so the Owner can judge its provenance.
    @param record: Immutable evidence record.
    @return: One display line for the evidence.
    """
    version = record.version.value if record.version is not None else "-"
    return (f"證據 id={record.evidence_id} source={record.source_type.value} retrieved={record.retrieved_at.isoformat()} "
            f"reason={record.reason} judge={record.judged_by} version={version}")


def _history_line(snapshot: ReviewSnapshot) -> str:
    """@description: Show one immutable review revision so the Owner can follow how the decision evolved.
    @param snapshot: Review revision from the audit history.
    @return: One display line for that revision.
    """
    return f"歷史 revision={snapshot.version} status={snapshot.status.value}"


def _evidence_source(kind: str) -> EvidenceSourceType:
    """@description: Map the typed evidence kind onto its audited source category.
    @param kind: Owner-typed owner or platform word.
    @return: Matching evidence source type.
    """
    if kind == "owner":
        return EvidenceSourceType.OWNER_REVIEW
    if kind == "platform":
        return EvidenceSourceType.PLATFORM_METADATA
    raise ValueError("證據種類必須是 owner 或 platform")


def _result_lines(label: str, snapshot: ReviewSnapshot) -> list[str]:
    """@description: Format the review snapshot returned by a confirmation.
    @param label: Chinese prefix saying whether this is a first confirmation or a retry.
    @param snapshot: Review state returned by the service.
    @return: One result line.
    """
    return [f"{label} status={snapshot.status.value} revision={snapshot.version}"]


def _comparison_lines(before, after) -> list[str]:
    """@description: Compare the protected review facts before and after a non-decision evidence write.
    @param before: Operation view read before the evidence write.
    @param after: Operation view read after the evidence write.
    @return: Status, identity, order and history comparison lines.
    """
    return [f"審查狀態 {_unchanged(before.review == after.review)} status={after.review.status.value}",
            f"身分 {_unchanged(before.identity == after.identity)} identity={after.identity.status.name}",
            f"受理順序 {_unchanged(before.accepted_sequence == after.accepted_sequence)} accepted_sequence={after.accepted_sequence}",
            f"審查歷史 {_unchanged(before.history == after.history)}"]


def _unchanged(same: bool) -> str:
    """@description: Name the comparison result in the Owner's language.
    @param same: Whether the protected fact stayed identical.
    @return: 未變 or 已改變.
    """
    return "未變" if same else "已改變"


def new_session() -> DemoSession:
    """@description: Create a fresh seeded synthetic session for the interactive loop or a test.
    @param: none
    @return: Independent demo session with no shared state.
    """
    return DemoSession()


def run_script(lines: list[str]) -> list[str]:
    """@description: Drive a session with a fixed list of command lines so tests and transcripts are reproducible.
    @param lines: Command lines, executed in order until quit.
    @return: Banner, echoed commands and every output line in order.
    """
    session = new_session()
    output = list(BANNER)  # [transcript]: Only lines produced by real service calls are appended.
    for line in lines:
        output.append(f"> {line}")
        output.extend(session.execute(line))
        if session.finished:
            break
    return output


def main() -> None:
    """@description: Run the interactive Owner demo until quit or end of input.
    @param: none
    @return: None.
    """
    session = new_session()
    for line in BANNER:
        print(line)
    while not session.finished:
        try:
            typed = input("owner> ")
        except EOFError:
            break
        for output_line in session.execute(typed):
            print(output_line)


if __name__ == "__main__":
    main()
