"""Serve a one-person, loopback-only HTML console that lets the Owner answer pending song cases in bulk instead of one Discord button at a time.

It is started on demand (python -m bot.owner_web), prints a one-time access code, forwards every answer to the shared decision core one case at a time,
and stops itself after a quiet period. It implements no review rule of its own; the acting Owner comes only from DISCORD_OWNER_USER_ID.
"""

import argparse
import asyncio
import json
import logging
import os
import secrets
import signal
import time
from collections import deque
from datetime import datetime, timezone
from functools import partial
from typing import Any, Callable

from aiohttp import web

from bot.owner_web_page import APP_CSS, APP_JS, INDEX_HTML
from bot.urls import InvalidVideoUrl, parse_youtube_url

LOGGER = logging.getLogger(__name__)
LOOPBACK_HOSTS = ("127.0.0.1", "::1")  # [bind allow-list]: The only addresses the console may listen on.
DEFAULT_PORT = 8765  # [default port]: Matches the documented SSH tunnel.
TOKEN_HEADER = "X-Console-Token"  # [access code header]: Custom header every API request must carry; it is never read from a URL.
IDLE_TIMEOUT_SECONDS = 30 * 60  # [idle limit]: The console ends itself after this long without an authenticated request.
IDLE_POLL_SECONDS = 15.0  # [idle check interval]: How often the idle limit is evaluated.
MAX_BODY_BYTES = 64 * 1024  # [request bound]: Larger bodies are refused before parsing.
MAX_BATCH_SIZE = 100  # [batch bound]: Most decisions accepted in one request.
MAX_OPERATION_ID_LENGTH = 200  # [identifier bound]: Longest operation identifier accepted from the page.
PAGE_SIZE = 50  # [paging]: Rows requested from the service per call while gathering the whole queue.
MAX_CASES = 2000  # [list bound]: Most cases returned; beyond this the list says it was cut short.
MAX_KNOWN_SONGS = 500  # [list bound]: Most known songs offered in the duplicate picker.
RATE_LIMIT_REQUESTS = 240  # [rate limit]: Requests allowed per window.
RATE_WINDOW_SECONDS = 60.0  # [rate window]: Length of the sliding window.
MAX_FAILED_ATTEMPTS = 10  # [lockout]: Wrong or missing access codes allowed per window before everything is refused.
SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; script-src 'self'; style-src 'self'; img-src https://i.ytimg.com data:; connect-src 'self'; "
                                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}  # [response hardening]: Applied to every response, including errors.
ERROR_MESSAGES = {
    400: ("BadRequest", "請求內容不正確。"), 401: ("Unauthorized", "存取碼不正確或沒有帶上。"), 403: ("Forbidden", "這個請求的來源不被允許。"),
    404: ("NotFound", "找不到這個項目。"), 405: ("MethodNotAllowed", "不支援這種請求方式。"), 413: ("PayloadTooLarge", "請求太大。"),
    415: ("UnsupportedMediaType", "請求格式必須是 JSON。"), 429: ("TooManyRequests", "請求太頻繁，請稍後再試。"),
}  # [fixed wording]: The only text an error response may carry besides an exception class name.
INTERNAL_MESSAGE = "伺服器處理時發生錯誤，請按「重新載入」再試。"  # [fixed wording]: Used for every unexpected failure.
FAILED_DECISION_TEXT = "這一筆處理時發生錯誤，沒有確認結果；請重新載入清單確認。"  # [fixed wording]: Per-case failure sentence.
NOT_PENDING_TEXT = "這一筆已經不在待處理清單裡（可能已被處理），沒有再動作。"  # [fixed wording]: Case vanished between list and submit.
DURATION_BLOCK_TEXT = "這首要先確認影片長度，網頁不能處理，請改用 Discord 或指令列。"  # [fixed wording]: needs_duration cases.
FROZEN_BLOCK_TEXT = "這首因為資料互相衝突而被凍結，網頁不能處理。"  # [fixed wording]: frozen_conflict cases.
NO_CHOICE_TEXT = "目前沒有可以回答的選項。"  # [fixed wording]: Neither answer is offered.
PICK_DUPLICATE_TEXT = "選「不是」時，請先選出重複的是哪一首。"  # [fixed wording]: Missing duplicate choice.
MAX_EXCHANGE_CODE_DIGITS = 12  # [input bound]: Longest operation code accepted from the search box.
EXCHANGE_FIELDS = ("operation_code", "requester", "delivery_state", "created_at", "refund_state", "reason", "waiting_seconds")  # [privacy whitelist]: The only lookup fields shown; no link, token or balance.
EXCHANGE_STATE_TEXT = {"reserved": "已預約", "sending": "傳送中", "delivered": "已送達", "failed": "失敗",
                       "uncertain": "結果不明"}  # [plain wording]: recommendations.status values; anything else falls back to the stored text.
EXCHANGE_REFUND_TEXT = {"refunded": "已退點", "refund_pending": "退點處理中", "not_needed": "不需退點"}  # [plain wording]: refund_state values.
EXCHANGE_REASON_TEXT = {"delivery_result_unknown": "傳送結果不明，無法確認使用者有沒有收到", "refund_pending": "退點還沒完成",
                        "sending_lease_expired": "傳送中斷，已超過等待時間", "reserved_not_sent": "已預約但還沒送出"}  # [plain wording]: stuck reasons.
EXCHANGE_NOT_FOUND_TEXT = "查不到這個編號的兌換紀錄。"  # [fixed wording]: No such exchange for the code.
ANSWERS = ("yes", "no")  # [answer vocabulary]: The only answers a request may carry.


class ConsoleRefused(RuntimeError):
    """Refuse to start the console; the message names the reason only."""


class ApiError(Exception):
    """Carry an HTTP status for a request the console refuses with a fixed sentence."""

    def __init__(self, status: int) -> None:
        """@description: Remember which fixed error response to send.
        @param self: Error instance.
        @param status: HTTP status key of ERROR_MESSAGES.
        @return: None.
        """
        super().__init__(status)
        self.status = status


def validate_bind_host(host: str) -> str:
    """@description: Allow only the two loopback literals so the console can never be reached from another machine.
    @param host: Requested listen address.
    @return: The same address when it is allowed.
    """
    if host not in LOOPBACK_HOSTS:
        raise ConsoleRefused("BIND_ADDRESS_NOT_LOOPBACK")
    return host


def validate_port(port: int) -> int:
    """@description: Require a real unprivileged-style port, because the Host check needs a known number.
    @param port: Requested port.
    @return: The same port when valid.
    """
    if not 1 <= port <= 65535:
        raise ConsoleRefused("PORT_OUT_OF_RANGE")
    return port


def new_access_token() -> str:
    """@description: Create the one-time access code for this run.
    @param: none
    @return: URL-safe random text with 256 bits of entropy.
    """
    return secrets.token_urlsafe(32)


class SlidingWindowLimiter:
    """Count events inside a moving time window so bursts and guessing are slowed down."""

    def __init__(self, max_events: int, window_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        """@description: Configure the budget and time source.
        @param self: Limiter instance.
        @param max_events: Events allowed inside the window.
        @param window_seconds: Window length in seconds.
        @param clock: Monotonic time source, replaceable in tests.
        @return: None.
        """
        self._max_events = max_events
        self._window_seconds = window_seconds
        self._clock = clock
        self._events: deque[float] = deque()  # [recent events]: Timestamps still inside the window.

    def exhausted(self) -> bool:
        """@description: Tell whether the budget is already used up, without recording anything.
        @param self: Limiter instance.
        @return: True when no further event fits.
        """
        self._forget_old_events()
        return len(self._events) >= self._max_events

    def record(self) -> None:
        """@description: Count one event now.
        @param self: Limiter instance.
        @return: None.
        """
        self._events.append(self._clock())

    def allow(self) -> bool:
        """@description: Count one event when the budget allows it.
        @param self: Limiter instance.
        @return: True when the event was admitted.
        """
        if self.exhausted():
            return False
        self.record()
        return True

    def _forget_old_events(self) -> None:
        """@description: Drop events that left the window.
        @param self: Limiter instance.
        @return: None.
        """
        oldest_allowed = self._clock() - self._window_seconds
        while self._events and self._events[0] <= oldest_allowed:
            self._events.popleft()


class IdleMonitor:
    """Remember the last authenticated activity so the console can end itself when nobody uses it."""

    def __init__(self, timeout_seconds: float = IDLE_TIMEOUT_SECONDS, clock: Callable[[], float] = time.monotonic,
                 poll_seconds: float = IDLE_POLL_SECONDS) -> None:
        """@description: Start the idle clock now.
        @param self: Monitor instance.
        @param timeout_seconds: Quiet period after which the console stops.
        @param clock: Monotonic time source, replaceable in tests.
        @param poll_seconds: How often the serving loop checks.
        @return: None.
        """
        self.poll_seconds = poll_seconds
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._last_activity = clock()  # [last use]: Reset by every authenticated request.

    def touch(self) -> None:
        """@description: Record activity now.
        @param self: Monitor instance.
        @return: None.
        """
        self._last_activity = self._clock()

    def is_expired(self) -> bool:
        """@description: Tell whether the quiet period has passed.
        @param self: Monitor instance.
        @return: True when the console should stop.
        """
        return self._clock() - self._last_activity > self._timeout_seconds


def _error_response(status: int, error_code: str | None = None, message: str | None = None) -> web.Response:
    """@description: Build the fixed-shape JSON error, never carrying a stack, secret or raw exception text.
    @param status: HTTP status.
    @param error_code: Error class or fixed code; defaults to the status's fixed code.
    @param message: Fixed sentence; defaults to the status's fixed sentence.
    @return: JSON response.
    """
    default_code, default_message = ERROR_MESSAGES.get(status, ("Error", INTERNAL_MESSAGE))
    return web.json_response({"error": error_code or default_code, "message": message or default_message}, status=status)


def _video_for(source_url: str):
    """@description: Parse a submitted link into a validated video, tolerating links the parser rejects.
    @param source_url: Link as accepted.
    @return: Parsed video, or None when the link is not a recognizable YouTube video.
    """
    try:
        return parse_youtube_url(source_url)
    except InvalidVideoUrl:
        return None


def _status_text(item: Any, choices: Any) -> str:
    """@description: Say in plain words where the case stands, from the core's flags and the stored review state.
    @param item: Pending item.
    @param choices: Core case choices.
    @return: Short plain-language status.
    """
    if choices.frozen_conflict:
        return "資料互相衝突，已凍結"
    if choices.needs_duration:
        return "等確認影片長度"
    if choices.no_requires_duplicate_pick:
        return "已通過，等確認是不是重複"
    return "等你決定" if item.review_status.value == "pending_review" else "等系統檢查完或等你決定"


def _blocked_reason(choices: Any) -> str:
    """@description: Explain why a case cannot be answered on this page.
    @param choices: Core case choices.
    @return: Fixed sentence, or empty text when the case can be answered.
    """
    if choices.needs_duration:
        return DURATION_BLOCK_TEXT
    if choices.frozen_conflict:
        return FROZEN_BLOCK_TEXT
    return "" if (choices.can_yes or choices.can_no) else NO_CHOICE_TEXT


def serialize_case(item: Any, choices: Any) -> dict:
    """@description: Turn a pending item and the core's choices into the page's JSON, exposing only what the card shows.
    @param item: Pending item.
    @param choices: Core case choices.
    @return: JSON-ready case.
    """
    video = _video_for(item.source_url)
    return {
        "operation_id": item.operation_id, "sequence": item.accepted_sequence, "submitter_id": item.submitter_id,
        "video_id": video.video_id if video else None, "link": video.canonical_url if video else None,
        "status_text": _status_text(item, choices), "doubt_text": choices.doubt_text, "question": choices.question,
        "yes_label": choices.yes_label, "no_label": choices.no_label, "can_yes": choices.can_yes, "can_no": choices.can_no,
        "needs_duration": choices.needs_duration, "frozen_conflict": choices.frozen_conflict,
        "requires_duplicate_pick": choices.no_requires_duplicate_pick, "blocked_reason": _blocked_reason(choices),
    }


def _parse_decision(raw: Any) -> dict:
    """@description: Validate one requested decision from the page.
    @param raw: Parsed JSON element.
    @return: Clean decision with operation_id, answer and duplicate_of_sequence.
    """
    if not isinstance(raw, dict):
        raise ApiError(400)
    operation_id, answer, duplicate = raw.get("operation_id"), raw.get("answer"), raw.get("duplicate_of_sequence")
    if not isinstance(operation_id, str) or not 0 < len(operation_id) <= MAX_OPERATION_ID_LENGTH or answer not in ANSWERS:
        raise ApiError(400)
    if duplicate is not None and (type(duplicate) is not int or duplicate < 0):
        raise ApiError(400)
    return {"operation_id": operation_id, "answer": answer, "duplicate_of_sequence": duplicate}


def parse_decisions(body: Any) -> list[dict]:
    """@description: Validate a whole batch request before any decision runs.
    @param body: Parsed JSON body.
    @return: Clean decisions in the requested order.
    """
    decisions = body.get("decisions") if isinstance(body, dict) else None
    if not isinstance(decisions, list) or not 0 < len(decisions) <= MAX_BATCH_SIZE:
        raise ApiError(400)
    parsed = [_parse_decision(raw) for raw in decisions]
    if len({decision["operation_id"] for decision in parsed}) != len(parsed):
        raise ApiError(400)
    return parsed


def _refusal_text(choices: Any, decision: dict) -> str:
    """@description: Check the requested answer against what the core offers, without judging the case itself.
    @param choices: Core case choices.
    @param decision: Validated decision.
    @return: Fixed refusal sentence, or empty text when the core offers this answer.
    """
    blocked = _blocked_reason(choices)
    if blocked:
        return blocked
    if not (choices.can_yes if decision["answer"] == "yes" else choices.can_no):
        return NO_CHOICE_TEXT
    if decision["answer"] == "no" and choices.no_requires_duplicate_pick and decision["duplicate_of_sequence"] is None:
        return PICK_DUPLICATE_TEXT
    return ""


def parse_exchange_code(raw_code: str | None) -> int:
    """@description: Accept only a plain positive integer from the search box.
    @param raw_code: Raw query text.
    @return: The operation code.
    """
    if raw_code is None or not raw_code.isascii() or not raw_code.isdecimal() or len(raw_code) > MAX_EXCHANGE_CODE_DIGITS or int(raw_code) < 1:
        raise ApiError(400)
    return int(raw_code)


def serialize_exchange(row: dict) -> dict:
    """@description: Keep only the whitelisted lookup fields and add a plain state label.
    @param row: Repository lookup row.
    @return: JSON-ready exchange summary.
    """
    exchange = {field: row.get(field) for field in EXCHANGE_FIELDS}
    if hasattr(exchange["created_at"], "isoformat"):
        exchange["created_at"] = exchange["created_at"].isoformat()
    for text_key, source_key, wording in (("state_text", "delivery_state", EXCHANGE_STATE_TEXT), ("refund_text", "refund_state", EXCHANGE_REFUND_TEXT),
                                          ("reason_text", "reason", EXCHANGE_REASON_TEXT)):
        stored = exchange[source_key]
        exchange[text_key] = "" if stored is None else wording.get(str(stored), str(stored))
    return exchange


def _failure(operation_id: str, error_class: str, text: str) -> dict:
    """@description: Shape one failed per-case result.
    @param operation_id: Case identifier.
    @param error_class: Fixed code or exception class name.
    @param text: Fixed sentence for the Owner.
    @return: JSON-ready result.
    """
    return {"operation_id": operation_id, "ok": False, "error_class": error_class, "text": text}


def _success(operation_id: str, result: Any) -> dict:
    """@description: Shape one successful per-case result from the core's outcome.
    @param operation_id: Case identifier.
    @param result: Core decision result.
    @return: JSON-ready result.
    """
    kind = getattr(result.kind, "value", result.kind)
    return {"operation_id": operation_id, "ok": True, "kind": str(kind), "text": result.text, "paid": bool(result.paid),
            "still_pending": bool(result.still_pending)}


class OwnerWebConsole:
    """Hold the review service, Owner identity, access code and limits for one console run, and answer its requests."""

    def __init__(self, service: Any, owner_id: int, access_token: str, port: int, core: Any, bind_host: str, limits: dict,
                 exchange_lookup: Callable | None = None) -> None:
        """@description: Gather the collaborators and request guards for this run.
        @param self: Console instance.
        @param service: Review service exposing list_pending and list_known_versions.
        @param owner_id: Owner identity from the environment, never from a request.
        @param access_token: One-time access code.
        @param port: Port being served, used by the Host check.
        @param core: Shared decision core module (case_choices, decide, Answer).
        @param bind_host: Loopback address being served.
        @param limits: Request limiter, failure limiter and idle monitor.
        @param exchange_lookup: Repository lookup (requester_id, operation_code) -> row or None, or None when unavailable.
        @return: None.
        """
        self._exchange_lookup = exchange_lookup
        self._service = service
        self._owner_id = owner_id
        self._access_token = access_token
        self._core = core
        self._allowed_hosts = self._host_allow_list(port, bind_host)  # [rebinding guard]: Only these Host values are served.
        self._requests: SlidingWindowLimiter = limits["requests"]
        self._failures: SlidingWindowLimiter = limits["failures"]
        self.idle: IdleMonitor = limits["idle"]
        self._tokens: dict = {}  # [retry tokens]: Kept for the process so a retry after an unknown outcome replays the same token.

    @staticmethod
    def _host_allow_list(port: int, bind_host: str) -> frozenset:
        """@description: List the Host header values that name this loopback server.
        @param port: Port being served.
        @param bind_host: Loopback address being served.
        @return: Lower-case Host values.
        """
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if bind_host == "::1":
            hosts.add(f"[::1]:{port}")
        return frozenset(hosts)

    @web.middleware
    async def middleware(self, request: web.Request, handler: Callable) -> web.StreamResponse:
        """@description: Run every guard, then the handler, and turn every failure into a fixed response with hardened headers.
        @param self: Console instance.
        @param request: Incoming request.
        @param handler: Route handler.
        @return: Response carrying the security headers.
        """
        try:
            self._admit(request)
            response = await handler(request)
        except ApiError as refusal:
            response = _error_response(refusal.status)
        except web.HTTPException as http_error:
            response = _error_response(http_error.status)
        except Exception as failure:  # [request boundary]: Log the class only; the page gets a fixed sentence.
            LOGGER.error("Request failed: %s", type(failure).__name__)
            response = _error_response(500, type(failure).__name__, INTERNAL_MESSAGE)
        response.headers.update(SECURITY_HEADERS)
        return response

    def _admit(self, request: web.Request) -> None:
        """@description: Apply the Host, Origin, size, rate and access-code guards in order.
        @param self: Console instance.
        @param request: Incoming request.
        @return: None; raises ApiError for the first refused guard.
        """
        self._check_host_and_origin(request)
        if not self._requests.allow():
            raise ApiError(429)
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            raise ApiError(413)
        if request.path.startswith("/api/"):
            self._check_access_code(request)

    def _check_host_and_origin(self, request: web.Request) -> None:
        """@description: Refuse DNS-rebinding and cross-site requests by requiring a loopback Host and a same-origin Origin.
        @param self: Console instance.
        @param request: Incoming request.
        @return: None; raises ApiError(403) when refused.
        """
        host = request.headers.get("Host", "").lower()
        if host not in self._allowed_hosts:
            raise ApiError(403)
        origin = request.headers.get("Origin")
        if origin is not None and origin.lower() != f"http://{host}":
            raise ApiError(403)

    def _check_access_code(self, request: web.Request) -> None:
        """@description: Require the access code in the custom header, compared in constant time, with a lockout after repeated misses.
        @param self: Console instance.
        @param request: Incoming API request.
        @return: None; raises ApiError(429) when locked out or ApiError(401) when wrong.
        """
        if self._failures.exhausted():
            raise ApiError(429)
        presented = request.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(presented.encode("utf-8"), self._access_token.encode("utf-8")):
            self._failures.record()
            raise ApiError(401)
        self.idle.touch()

    async def _gather(self, reader: Callable, key: str, cap: int) -> tuple[list, bool]:
        """@description: Read every page of a keyset listing in a worker thread, up to a bound.
        @param self: Console instance.
        @param reader: Service listing method taking after_sequence and limit.
        @param key: Attribute holding each row's sequence, used to sort.
        @param cap: Most rows to gather.
        @return: Rows in acceptance order, and whether the listing was cut short.
        """
        rows: list = []
        cursor = None
        while True:
            page = await asyncio.to_thread(reader, self._owner_id, after_sequence=cursor, limit=PAGE_SIZE)
            rows.extend(page.items)
            if page.next_after_sequence is None:
                return sorted(rows, key=lambda row: getattr(row, key)), False
            if len(rows) >= cap:
                return sorted(rows[:cap], key=lambda row: getattr(row, key)), True
            cursor = page.next_after_sequence

    async def _load_queue(self) -> tuple[list, list, bool]:
        """@description: Read the pending cases and the known songs once.
        @param self: Console instance.
        @return: Pending items, known songs and whether the pending list was cut short.
        """
        pending, truncated = await self._gather(self._service.list_pending, "accepted_sequence", MAX_CASES)
        known, _ = await self._gather(self._service.list_known_versions, "first_sequence", MAX_KNOWN_SONGS)
        return pending, known, truncated

    @staticmethod
    def _sequence_by_video(pending: list, known: list) -> dict:
        """@description: Map existing video ids to their accepted sequence so doubts can name them as numbers.
        @param pending: Pending items.
        @param known: Known songs.
        @return: Video id to accepted sequence.
        """
        mapping = {row.first_video_id: row.first_sequence for row in known}
        for item in pending:
            video = _video_for(item.source_url)
            if video is not None:
                mapping.setdefault(video.video_id, item.accepted_sequence)
        return mapping

    async def list_cases(self, request: web.Request) -> web.Response:
        """@description: Answer the page's list request with every pending case and the duplicate choices.
        @param self: Console instance.
        @param request: Authenticated GET request.
        @return: JSON with cases, known songs and a truncation flag.
        """
        pending, known, truncated = await self._load_queue()
        sequences = self._sequence_by_video(pending, known)
        cases = [serialize_case(item, self._core.case_choices(item, sequences)) for item in pending]
        known_songs = [{"sequence": row.first_sequence, "video_id": row.first_video_id, "link": _watch_link(row.first_video_id)} for row in known]
        return web.json_response({"cases": cases, "known": known_songs, "truncated": truncated})

    async def exchange(self, request: web.Request) -> web.Response:
        """@description: Let the Owner look up one exchange by the number a user reported, showing status only.
        @param self: Console instance.
        @param request: Authenticated GET request with a code query.
        @return: JSON with found flag and the whitelisted summary, or a fixed not-found sentence.
        """
        code = parse_exchange_code(request.query.get("code"))
        if self._exchange_lookup is None:
            raise ApiError(404)
        row = await asyncio.to_thread(self._exchange_lookup, self._owner_id, code)
        if row is None:
            return web.json_response({"found": False, "message": EXCHANGE_NOT_FOUND_TEXT})
        return web.json_response({"found": True, "exchange": serialize_exchange(row)})

    async def decide(self, request: web.Request) -> web.Response:
        """@description: Run the requested decisions one at a time through the shared core and report each outcome separately.
        @param self: Console instance.
        @param request: Authenticated POST request carrying a JSON batch.
        @return: JSON with one result per requested decision, in order.
        """
        decisions = parse_decisions(await _read_json(request))
        pending, known, _ = await self._load_queue()
        items = {item.operation_id: item for item in pending}
        sequences = self._sequence_by_video(pending, known)
        results = [await self._decide_one(decision, items.get(decision["operation_id"]), sequences) for decision in decisions]
        return web.json_response({"results": results})

    async def _decide_one(self, decision: dict, item: Any, sequences: dict) -> dict:
        """@description: Apply one decision, converting any failure into that case's own result so the rest of the batch continues.
        @param self: Console instance.
        @param decision: Validated decision.
        @param item: Current pending item, or None when it left the queue.
        @param sequences: Video id to accepted sequence map for the core's wording.
        @return: JSON-ready result.
        """
        operation_id = decision["operation_id"]
        if item is None:
            return _failure(operation_id, "NotPending", NOT_PENDING_TEXT)
        try:
            choices = self._core.case_choices(item, sequences)
            refusal = _refusal_text(choices, decision)
            if refusal:
                return _failure(operation_id, "Refused", refusal)
            result = await asyncio.to_thread(self._run_core_decision, item, decision)
        except Exception as failure:  # [per-case boundary]: One failure must not stop the batch; the class is logged, never the message.
            LOGGER.error("Decision failed: %s", type(failure).__name__)
            return _failure(operation_id, type(failure).__name__, FAILED_DECISION_TEXT)
        return _success(operation_id, result)

    def _run_core_decision(self, item: Any, decision: dict) -> Any:
        """@description: Call the blocking shared core for one case with the configured Owner.
        @param self: Console instance.
        @param item: Pending item at its current revision.
        @param decision: Validated decision.
        @return: Core decision result.
        """
        answer = self._core.Answer[decision["answer"].upper()]
        duplicate = decision["duplicate_of_sequence"] if decision["answer"] == "no" else None
        return self._core.decide(self._service, self._owner_id, item, answer, duplicate_of_sequence=duplicate, tokens=self._tokens)


def _watch_link(video_id: str) -> str:
    """@description: Build the canonical watch link of a stored video id for the duplicate picker.
    @param video_id: Stored video id.
    @return: Watch URL.
    """
    return f"https://www.youtube.com/watch?v={video_id}"


async def _read_json(request: web.Request) -> Any:
    """@description: Read a bounded JSON request body, refusing other content types.
    @param request: Incoming POST request.
    @return: Parsed JSON value.
    """
    if request.content_type != "application/json":
        raise ApiError(415)
    raw_body = await request.read()
    try:
        return json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        raise ApiError(400) from None


def _static_handler(body: str, content_type: str) -> Callable:
    """@description: Build a handler that serves one fixed text asset.
    @param body: Asset text.
    @param content_type: Media type.
    @return: Route handler.
    """
    async def serve(request: web.Request) -> web.Response:
        """@description: Return the fixed asset.
        @param request: Incoming request.
        @return: Text response.
        """
        return web.Response(text=body, content_type=content_type, charset="utf-8")
    return serve


def _default_core() -> Any:
    """@description: Load the shared decision core lazily so importing this module stays light.
    @param: none
    @return: The bot.owner_decisions module.
    """
    from bot import owner_decisions
    return owner_decisions


def build_console_app(service: Any, owner_id: int, access_token: str, port: int, *, core: Any = None, bind_host: str = "127.0.0.1",
                      idle: IdleMonitor | None = None, rate_limit_requests: int = RATE_LIMIT_REQUESTS,
                      max_failed_attempts: int = MAX_FAILED_ATTEMPTS, exchange_lookup: Callable | None = None) -> web.Application:
    """@description: Assemble the console application with its guards and routes, touching no network or database.
    @param service: Review service exposing list_pending and list_known_versions.
    @param owner_id: Owner identity from the environment.
    @param access_token: One-time access code.
    @param port: Port that will be served.
    @param core: Decision core; defaults to bot.owner_decisions.
    @param bind_host: Loopback address that will be served.
    @param idle: Idle monitor shared with the serving loop, or None for a default one.
    @param rate_limit_requests: Requests allowed per window.
    @param max_failed_attempts: Wrong access codes allowed per window.
    @param exchange_lookup: Repository exchange lookup, or None to disable the search box.
    @return: Application ready to serve.
    """
    limits = {"requests": SlidingWindowLimiter(rate_limit_requests, RATE_WINDOW_SECONDS),
              "failures": SlidingWindowLimiter(max_failed_attempts, RATE_WINDOW_SECONDS), "idle": idle or IdleMonitor()}
    console = OwnerWebConsole(service, owner_id, access_token, port, core or _default_core(), bind_host, limits, exchange_lookup)
    app = web.Application(middlewares=[console.middleware], client_max_size=MAX_BODY_BYTES)
    app.router.add_get("/", _static_handler(INDEX_HTML, "text/html"))
    app.router.add_get("/app.js", _static_handler(APP_JS, "application/javascript"))
    app.router.add_get("/app.css", _static_handler(APP_CSS, "text/css"))
    app.router.add_get("/api/cases", console.list_cases)
    app.router.add_get("/api/exchange", console.exchange)
    app.router.add_post("/api/decide", console.decide)
    return app


async def _wait_until_stopped(stop_event: asyncio.Event, idle: IdleMonitor | None) -> None:
    """@description: Block until a stop is requested or the idle limit passes.
    @param stop_event: Set by a signal or a test.
    @param idle: Idle monitor, or None to wait for the stop event only.
    @return: None.
    """
    if idle is None:
        await stop_event.wait()
        return
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=idle.poll_seconds)
        except asyncio.TimeoutError:
            if idle.is_expired():
                LOGGER.info("Owner web console stopping: idle limit reached")
                return


async def serve_console(app: web.Application, host: str, port: int, stop_event: asyncio.Event, ready_event: asyncio.Event | None = None,
                        idle: IdleMonitor | None = None) -> None:
    """@description: Listen on a loopback address, with the access log off, until stopped or idle.
    @param app: Console application.
    @param host: Listen address; must be loopback.
    @param port: Listen port.
    @param stop_event: Set to request shutdown.
    @param ready_event: Set once the socket is listening, or None.
    @param idle: Idle monitor ending the run, or None.
    @return: None; the socket is closed before returning.
    """
    validate_bind_host(host)
    runner = web.AppRunner(app, access_log=None)  # [no access log]: Request lines would otherwise reach the log.
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
        if ready_event is not None:
            ready_event.set()
        await _wait_until_stopped(stop_event, idle)
    finally:
        await runner.cleanup()


def _utc_now() -> datetime:
    """@description: Supply the review clock so previews expire by real time.
    @param: none
    @return: Current timezone-aware UTC instant.
    """
    return datetime.now(timezone.utc)


def load_console_dependencies() -> tuple[Any, int, Callable | None]:
    """@description: Build the same Owner review service the Bot uses, from the same environment, with the Owner identity taken only from it.
    @param: none
    @return: Review service, the configured Owner identifier and the repository's exchange lookup (None when it has none).
    """
    # [deferred imports]: The database stack loads only when the console really starts, so the guards stay importable and testable without it.
    from bot.__main__ import verify_mounted_secrets
    from bot.auto_startup import build_auto_pipeline, load_auto_settings
    from bot.config import load_database_settings
    from bot.database.connection import open_connection
    from bot.database.submission_repository import SubmissionRepository
    from bot.owner_cli import TOKEN_TTL, owner_identity
    from bot.owner_review import OwnerReviewService
    owner_id = owner_identity(os.environ)
    database_settings = load_database_settings()
    verify_mounted_secrets()
    auto_settings = load_auto_settings()
    repository = SubmissionRepository(partial(open_connection, database_settings), owner_id,
                                      automatic_settlement=auto_settings.settlement_enabled, api_retention=auto_settings.api_retention)
    pipeline = build_auto_pipeline(repository, auto_settings)
    followup = pipeline.owner_settlement_note if pipeline is not None and auto_settings.settlement_enabled else None
    service = OwnerReviewService(repository, owner_id, _utc_now, token_ttl=TOKEN_TTL, settlement_followup=followup)
    return service, owner_id, getattr(repository, "owner_exchange_lookup", None)


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """@description: Read the command line.
    @param argv: Arguments, or None for the process arguments.
    @return: Parsed host and port.
    """
    parser = argparse.ArgumentParser(prog="python -m bot.owner_web", description="Local Owner review console (loopback only).")
    parser.add_argument("--host", default="127.0.0.1", help="loopback address only: 127.0.0.1 or ::1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args(argv)


def _install_stop_signals(stop_event: asyncio.Event) -> None:
    """@description: Make Ctrl+C and SIGTERM end the console cleanly where the platform allows.
    @param stop_event: Event the serving loop waits on.
    @return: None.
    """
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop_event.set)
        except NotImplementedError:
            return


async def _run(host: str, port: int, service: Any, owner_id: int, exchange_lookup: Callable | None) -> None:
    """@description: Print the one-time address and serve until stopped or idle.
    @param host: Validated loopback address.
    @param port: Validated port.
    @param service: Review service.
    @param owner_id: Owner identifier.
    @param exchange_lookup: Repository exchange lookup, or None.
    @return: None.
    """
    access_token = new_access_token()
    idle = IdleMonitor()
    app = build_console_app(service, owner_id, access_token, port, bind_host=host, idle=idle, exchange_lookup=exchange_lookup)
    stop_event = asyncio.Event()
    _install_stop_signals(stop_event)
    print(f"Owner 審核網頁已啟動（閒置 {IDLE_TIMEOUT_SECONDS // 60} 分鐘自動關閉，Ctrl+C 可立即關閉）。", flush=True)
    print(f"請在 SSH 通道另一端的瀏覽器開啟：http://localhost:{port}/#token={access_token}", flush=True)
    await serve_console(app, host, port, stop_event, idle=idle)


def _load_or_exit() -> tuple[Any, int, Callable | None]:
    """@description: Load the service, ending the process with a reason code only when configuration is missing or refused.
    @param: none
    @return: Review service, Owner identifier and exchange lookup.
    """
    from bot.config import ConfigurationError
    from bot.owner_cli import OperatorRefused
    try:
        return load_console_dependencies()
    except (ConfigurationError, OperatorRefused) as refusal:
        raise SystemExit(f"Owner web console refused to start: {refusal}") from None


def main(argv: list[str] | None = None) -> None:
    """@description: Validate the bind address and port before reading any setting, then serve the console.
    @param argv: Command-line arguments, or None for the process arguments.
    @return: None; refusals end the process with a reason code only.
    """
    arguments = _parse_arguments(argv)
    try:
        host = validate_bind_host(arguments.host)
        port = validate_port(arguments.port)
    except ConsoleRefused as refusal:
        raise SystemExit(f"Owner web console refused to start: {refusal}") from None
    service, owner_id, exchange_lookup = _load_or_exit()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run(host, port, service, owner_id, exchange_lookup))


if __name__ == "__main__":
    main()
