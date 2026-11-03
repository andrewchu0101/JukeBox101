"""Read how automatic review decisions were reasoned and which cases now wait for the Owner.

Inputs are a transaction-owned cursor and a look-back window; outputs are reason-code counts and a short pending list of codes and video IDs.
Nothing here writes, and no official title, channel or duration is stored or read.
"""

import re
from typing import Any

from bot.auto_review import AUTO_REVIEW_DECISION_PREFIX
from bot.database.review_persistence import AWAITS_OWNER_SQL, EARLIEST_EVENT_SQL
from bot.version_concerns import parse_concern_detail

MIN_WINDOW_DAYS = 1  # [window floor]: The shortest report period.
MAX_WINDOW_DAYS = 60  # [window cap]: The longest report period.
MAX_PENDING_CASES = 20  # [list bound]: Only the oldest waiting cases are listed.
UNPARSED_REASON_CODE = "other"  # [fallback bucket]: Stored reasons without a readable reason code.
REASON_CODE_PATTERN = re.compile(r"reason=(\S+)")  # [audit format]: Reads the code written by the automatic review.


def review_reason_report(cursor: Any, days: int) -> dict:
    """@description: Give the Owner a read-only view of why automatic review decided as it did, so rules can be tuned from evidence.
    @param cursor: Transaction-owned cursor; nothing is written.
    @param days: Look-back window for the counts, from one to sixty.
    @return: reason_counts (code to count) and pending (oldest waiting cases with code, reason and concern codes).
    """
    if not MIN_WINDOW_DAYS <= days <= MAX_WINDOW_DAYS:
        raise ValueError("DAYS_OUT_OF_RANGE")
    return {"reason_counts": _reason_counts(cursor, days), "pending": _pending_cases(cursor)}


def _reason_counts(cursor: Any, days: int) -> dict:
    """@description: Count automatic decisions per stored reason code inside the window.
    @param cursor: Transaction-owned cursor.
    @param days: Look-back window in days.
    @return: Count per reason code; unreadable reasons fall under the fallback code.
    """
    cursor.execute("SELECT COALESCE(substring(decision_reason from 'reason=([^ ]+)'), %s) AS code, count(*) AS total FROM jb2_review_history "
                   "WHERE starts_with(decision_reason, %s) AND created_at >= statement_timestamp() - make_interval(days => %s) "
                   "GROUP BY 1 ORDER BY 2 DESC, 1", (UNPARSED_REASON_CODE, AUTO_REVIEW_DECISION_PREFIX, days))
    return {row["code"]: row["total"] for row in cursor.fetchall()}


def _pending_cases(cursor: Any) -> list[dict]:
    """@description: List the oldest cases still waiting for the Owner with the reason of their newest automatic decision.
    @param cursor: Transaction-owned cursor.
    @return: Cases in acceptance order, at most the list bound.
    """
    cursor.execute("SELECT e.operation_id, e.accepted_sequence, e.video_id, (SELECT h.decision_reason FROM jb2_review_history h "
                   "WHERE h.video_id = e.video_id AND starts_with(h.decision_reason, %s) ORDER BY h.revision DESC LIMIT 1) AS decision_reason "
                   "FROM jb2_events e JOIN jb2_videos v ON v.video_id = e.video_id "
                   f"WHERE {AWAITS_OWNER_SQL} AND {EARLIEST_EVENT_SQL} ORDER BY e.accepted_sequence LIMIT %s", (AUTO_REVIEW_DECISION_PREFIX, MAX_PENDING_CASES))
    return [_pending_case(row) for row in cursor.fetchall()]


def _pending_case(row: dict) -> dict:
    """@description: Reduce one queue row to codes and identifiers only.
    @param row: Queue row with the newest automatic reason text, or None.
    @return: Case with operation, order, video, reason code and distinct concern codes.
    """
    stored = row["decision_reason"] or ""
    found = REASON_CODE_PATTERN.search(stored)
    concern_codes = list(dict.fromkeys(concern.code.value for concern in parse_concern_detail(stored)))
    return {"accepted_sequence": row["accepted_sequence"], "operation_id": row["operation_id"], "video_id": row["video_id"],
            "reason": found.group(1) if found else UNPARSED_REASON_CODE, "concerns": concern_codes}
