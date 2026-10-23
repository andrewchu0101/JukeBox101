"""Read one accepted operation's review projection and append Owner reexamination evidence without changing decisions.

Projections join the current event and video with append-only history; evidence appends reuse the audited decision key replay.
"""

import json
from dataclasses import dataclass
from typing import Any

from bot.database.persisted_snapshot import decode_current_identity, decode_current_review
from bot.database.review_persistence import decision_digest, replay_decision, save_decision
from bot.database.review_persistence import load_content_judgements
from bot.song_judgement import ContentJudgement
from bot.database.submission_codec import decode_contract, encode_contract
from bot.evidence_record import EvidenceRecord
from bot.review import ReviewSnapshot
from bot.version_identity import VersionAssignment


@dataclass(frozen=True)
class ReviewProjection:
    """Carry one operation's current review facts together with their audit trail."""

    operation_id: str  # [review subject]: Accepted operation whose video is being inspected.
    accepted_sequence: int  # [intake order]: Unchanged by reexamination or later review decisions.
    identity: VersionAssignment  # [current identity]: Persisted Owner-confirmed or conflicting assertion.
    review: ReviewSnapshot  # [current review]: Authoritative revision and status for the video.
    evidence: tuple[EvidenceRecord, ...]  # [audit evidence]: Reexamination facts only, oldest first.
    history: tuple[ReviewSnapshot, ...]  # [review history]: Every persisted revision, oldest first.
    content_judgements: tuple[ContentJudgement, ...] = ()  # [song judgements]: Complete approval judgements, oldest first.


def review_projection(cursor: Any, operation_id: str) -> ReviewProjection:
    """@description: Give the Owner one consistent view of an operation without exposing private payloads.
    @param cursor: Transaction cursor holding the global writer gate.
    @param operation_id: Accepted operation identifier.
    @return: Current projection with evidence and history for the operation's video.
    """
    cursor.execute("SELECT e.*,v.identity_json,v.review_json,v.review_revision,v.review_status "
                   "FROM jb2_events e JOIN jb2_videos v ON v.video_id=e.video_id WHERE e.operation_id=%s", (operation_id,))
    row = cursor.fetchone()  # [current subject]: Missing operations are never inferred from history.
    if row is None:
        raise ValueError("Unknown review operation")
    cursor.execute("SELECT review_json FROM jb2_review_history WHERE video_id=%s ORDER BY revision", (row["video_id"],))
    history = tuple(decode_contract(item["review_json"], ReviewSnapshot) for item in cursor.fetchall())
    cursor.execute("SELECT evidence_json FROM jb2_evidence WHERE video_id=%s ORDER BY evidence_id", (row["video_id"],))
    evidence = tuple(_evidence_records(cursor.fetchall()))
    return ReviewProjection(row["operation_id"], row["accepted_sequence"], decode_current_identity(row),
                            decode_current_review(row), evidence, history,
                            load_content_judgements(cursor, row["video_id"]))


def append_evidence(cursor: Any, decision_id: str, operation_id: str, actor_id: int, reason: str,
                    evidence: EvidenceRecord) -> EvidenceRecord:
    """@description: Keep new recording evidence as an audit fact that leaves decisions, identity and order unchanged.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param decision_id: Idempotency key for this reexamination entry.
    @param operation_id: Accepted operation whose video the evidence must describe.
    @param actor_id: Authenticated Owner who judged the evidence.
    @param reason: Audit explanation for the reexamination.
    @param evidence: Validated evidence record about the same video.
    @return: Original or newly stored evidence record.
    """
    digest = decision_digest((operation_id, actor_id, reason, evidence))
    previous = replay_decision(cursor, decision_id, digest)  # [replay]: Repeated entries never duplicate audit rows.
    if previous is not None:
        return decode_contract(previous, EvidenceRecord)
    video_id = _operation_video_id(cursor, operation_id)
    if evidence.video.video_id != video_id or evidence.judged_by != actor_id:
        raise ValueError("Evidence must describe this video and be judged by its Owner")
    cursor.execute("INSERT INTO jb2_evidence (video_id,evidence_json,actor_id,decision_reason) VALUES (%s,%s,%s,%s)",
                   (video_id, encode_contract(evidence), actor_id, reason))
    save_decision(cursor, decision_id, digest, actor_id, reason, evidence)
    return evidence


def _operation_video_id(cursor: Any, operation_id: str) -> str:
    """@description: Resolve the video subject of an accepted operation before appending evidence.
    @param cursor: Transaction cursor.
    @param operation_id: Accepted operation identifier.
    @return: Platform video identifier of the operation.
    """
    cursor.execute("SELECT video_id FROM jb2_events WHERE operation_id=%s", (operation_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError("Unknown review operation")
    return row["video_id"]


def _evidence_records(rows: list[dict]) -> list[EvidenceRecord]:
    """@description: Keep reexamination evidence separate from persisted identity-and-review audit payloads.
    @param rows: Append-only audit rows for one video.
    @return: Decoded evidence records in stored order.
    """
    return [decode_contract(row["evidence_json"], EvidenceRecord) for row in rows if _is_evidence_record(row["evidence_json"])]


def _is_evidence_record(payload: str) -> bool:
    """@description: Recognize evidence payloads without treating review-revision audit tuples as evidence.
    @param payload: Stored JSON contract text.
    @return: Whether the top-level value is an EvidenceRecord contract.
    """
    value = json.loads(payload)["value"]
    return isinstance(value, dict) and value.get("contract") == EvidenceRecord.__name__
