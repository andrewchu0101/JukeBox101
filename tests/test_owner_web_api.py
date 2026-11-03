"""Offline tests for the Owner web console list, filter flags and decision endpoints.

The shared decision core and the review service are replaced by fakes, so no database, Discord or browser is touched; the console only forwards to the core.
"""

import asyncio
import socket
import threading
from enum import Enum
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from bot.owner_web import TOKEN_HEADER, build_console_app

ACCESS_TOKEN = "unit-test-access-token-value-0123456789abcdef"
OWNER_ID = 424242
VIDEO_A = "aaaaaaaaaaa"
VIDEO_B = "bbbbbbbbbbb"
VIDEO_C = "ccccccccccc"
VIDEO_D = "ddddddddddd"


class FakeAnswer(Enum):
    """Mirror the core's two answers."""

    YES = "yes"
    NO = "no"


class FakeKind(Enum):
    """Mirror a core outcome kind."""

    PAID = "paid"


def make_item(operation_id: str, video_id: str | None, sequence: int, submitter_id: int = 7) -> SimpleNamespace:
    """@description: Build a pending-case stand-in with the attributes the console reads.
    @param operation_id: Operation identifier.
    @param video_id: YouTube video id, or None for an unparseable link.
    @param sequence: Accepted sequence.
    @param submitter_id: Submitting user.
    @return: Namespace shaped like a pending item.
    """
    url = f"https://youtu.be/{video_id}" if video_id else "https://example.com/not-youtube"
    return SimpleNamespace(operation_id=operation_id, source_url=url, accepted_sequence=sequence, submitter_id=submitter_id,
                           review_status=SimpleNamespace(value="pending_review"), revision=1)


def make_choices(**overrides) -> SimpleNamespace:
    """@description: Build the core's case choices with sensible defaults.
    @param overrides: Fields to change.
    @return: Namespace shaped like the core's choices.
    """
    fields = dict(can_yes=True, can_no=True, needs_duration=False, frozen_conflict=False, no_requires_duplicate_pick=False,
                  doubt_text="", question="這是一首新的歌嗎？", yes_label="是，發獎", no_label="不是")
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeCore:
    """Record every decision call and answer from a script, never applying any rule."""

    Answer = FakeAnswer

    def __init__(self, choices_by_operation: dict | None = None, failing_operations: frozenset = frozenset()) -> None:
        """@description: Prepare scripted choices and failures.
        @param choices_by_operation: Choices keyed by operation id; others use defaults.
        @param failing_operations: Operations whose decision raises.
        @return: None.
        """
        self.choices_by_operation = choices_by_operation or {}
        self.failing_operations = failing_operations
        self.calls: list[dict] = []
        self.threads: list[int] = []

    def case_choices(self, item, sequence_by_video_id=None) -> SimpleNamespace:
        """@description: Return scripted choices for the item.
        @param item: Pending item.
        @param sequence_by_video_id: Ignored map.
        @return: Choices.
        """
        return self.choices_by_operation.get(item.operation_id, make_choices())

    def decide(self, service, actor_id, item, answer, *, duplicate_of_sequence=None, tokens=None) -> SimpleNamespace:
        """@description: Record the call and return a scripted result or raise.
        @param service: Service passed through.
        @param actor_id: Owner id passed through.
        @param item: Pending item.
        @param answer: Answer enum.
        @param duplicate_of_sequence: Chosen duplicate.
        @param tokens: Shared token dict.
        @return: Result namespace.
        """
        self.threads.append(threading.get_ident())
        self.calls.append(dict(service=service, actor_id=actor_id, operation_id=item.operation_id, answer=answer,
                               duplicate_of_sequence=duplicate_of_sequence, tokens=tokens))
        if item.operation_id in self.failing_operations:
            raise RuntimeError("database-password-leak")
        return SimpleNamespace(kind=FakeKind.PAID, text=f"已處理 {item.operation_id}", paid=answer is FakeAnswer.YES, still_pending=False)


class FakeService:
    """Serve scripted pending and known-song pages."""

    def __init__(self, pending_items: list, known_versions: list | None = None, page_size: int = 50, fail: bool = False) -> None:
        """@description: Prepare scripted data.
        @param pending_items: Pending items in acceptance order.
        @param known_versions: Known-song stand-ins.
        @param page_size: Rows per page, to exercise paging.
        @param fail: Make listing raise.
        @return: None.
        """
        self.pending_items = pending_items
        self.known_versions = known_versions or []
        self.page_size = page_size
        self.fail = fail

    def list_pending(self, actor_id, *, after_sequence=None, limit=50) -> SimpleNamespace:
        """@description: Return one keyset page of pending items.
        @param actor_id: Owner id.
        @param after_sequence: Cursor.
        @param limit: Requested page size.
        @return: Page namespace.
        """
        if self.fail:
            raise RuntimeError("db-host.internal:5432")
        return self._page(self.pending_items, after_sequence, "accepted_sequence")

    def list_known_versions(self, actor_id, *, after_sequence=None, limit=50) -> SimpleNamespace:
        """@description: Return one keyset page of known songs.
        @param actor_id: Owner id.
        @param after_sequence: Cursor.
        @param limit: Requested page size.
        @return: Page namespace.
        """
        return self._page(self.known_versions, after_sequence, "first_sequence")

    def _page(self, rows: list, after_sequence, key: str) -> SimpleNamespace:
        """@description: Slice rows after a cursor.
        @param rows: All rows in order.
        @param after_sequence: Cursor.
        @param key: Attribute holding the sequence.
        @return: Page namespace.
        """
        remaining = [row for row in rows if after_sequence is None or getattr(row, key) > after_sequence]
        page = remaining[:self.page_size]
        cursor = getattr(page[-1], key) if len(remaining) > self.page_size else None
        return SimpleNamespace(items=tuple(page), next_after_sequence=cursor)


def free_port() -> int:
    """@description: Reserve a currently free loopback port so the Host check can be configured before the server starts.
    @param: none
    @return: Port number.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def authed(extra: dict | None = None) -> dict:
    """@description: Build request headers carrying the access token.
    @param extra: Additional headers.
    @return: Header mapping.
    """
    return {TOKEN_HEADER: ACCESS_TOKEN, **(extra or {})}


def run_console(scenario, service, core=None, **app_options):
    """@description: Serve the console in a test server and run one async scenario against it.
    @param scenario: Coroutine function taking the client.
    @param service: Fake review service.
    @param core: Fake decision core.
    @param app_options: Extra build options.
    @return: The scenario's result.
    """
    port = free_port()
    app = build_console_app(service, OWNER_ID, ACCESS_TOKEN, port, core=core or FakeCore(), **app_options)

    async def go():
        async with TestClient(TestServer(app, host="127.0.0.1", port=port)) as client:
            return await scenario(client)
    return asyncio.run(go())


def standard_service() -> FakeService:
    """@description: Build a service with four pending cases and two known songs.
    @param: none
    @return: Fake service.
    """
    items = [make_item("op-1", VIDEO_A, 1), make_item("op-2", VIDEO_B, 2), make_item("op-3", VIDEO_C, 3), make_item("op-4", None, 4)]
    known = [SimpleNamespace(version_id="v1", first_sequence=9, first_video_id=VIDEO_D)]
    return FakeService(items, known)


async def fetch_cases(client) -> dict:
    """@description: Fetch the case list with the access token.
    @param client: Test client.
    @return: Parsed JSON body.
    """
    response = await client.get("/api/cases", headers=authed())
    assert response.status == 200
    return await response.json()


async def post_decisions(client, decisions: list, expected_status: int = 200) -> dict:
    """@description: Post a batch of decisions.
    @param client: Test client.
    @param decisions: Decision objects.
    @param expected_status: Status the caller expects.
    @return: Parsed JSON body.
    """
    response = await client.post("/api/decide", json={"decisions": decisions}, headers=authed())
    assert response.status == expected_status
    return await response.json()


def test_list_returns_cases_in_acceptance_order_with_link_and_thumbnail_id() -> None:
    body = run_console(fetch_cases, standard_service())
    assert [case["operation_id"] for case in body["cases"]] == ["op-1", "op-2", "op-3", "op-4"]
    first = body["cases"][0]
    assert first["video_id"] == VIDEO_A
    assert first["link"] == f"https://www.youtube.com/watch?v={VIDEO_A}"
    assert first["submitter_id"] == 7
    assert first["sequence"] == 1


def test_unparseable_link_has_no_link_or_thumbnail() -> None:
    body = run_console(fetch_cases, standard_service())
    assert body["cases"][3]["link"] is None
    assert body["cases"][3]["video_id"] is None


def test_list_carries_core_wording_and_flags_for_filters() -> None:
    core = FakeCore({"op-1": make_choices(doubt_text="標題很像 #12"), "op-2": make_choices(needs_duration=True, can_yes=False, can_no=False),
                     "op-3": make_choices(frozen_conflict=True, can_yes=False, can_no=False),
                     "op-4": make_choices(no_requires_duplicate_pick=True)})
    body = run_console(fetch_cases, standard_service(), core)
    cases = {case["operation_id"]: case for case in body["cases"]}
    assert cases["op-1"]["doubt_text"] == "標題很像 #12" and cases["op-1"]["question"] == "這是一首新的歌嗎？"
    assert cases["op-1"]["yes_label"] == "是，發獎" and cases["op-1"]["no_label"] == "不是"
    assert cases["op-2"]["needs_duration"] is True and cases["op-2"]["blocked_reason"]
    assert cases["op-3"]["frozen_conflict"] is True and cases["op-3"]["blocked_reason"]
    assert cases["op-4"]["requires_duplicate_pick"] is True and cases["op-4"]["blocked_reason"] == ""
    assert cases["op-1"]["blocked_reason"] == ""


def test_list_includes_known_songs_for_the_duplicate_picker() -> None:
    body = run_console(fetch_cases, standard_service())
    assert body["known"] == [{"sequence": 9, "video_id": VIDEO_D, "link": f"https://www.youtube.com/watch?v={VIDEO_D}"}]


def test_list_gathers_every_page() -> None:
    items = [make_item(f"op-{number}", VIDEO_A, number) for number in range(1, 8)]
    body = run_console(fetch_cases, FakeService(items, page_size=3))
    assert [case["sequence"] for case in body["cases"]] == [1, 2, 3, 4, 5, 6, 7]
    assert body["truncated"] is False


def test_single_yes_forwards_to_core_with_configured_owner_and_shared_tokens() -> None:
    core = FakeCore()
    service = standard_service()

    async def scenario(client) -> dict:
        return await post_decisions(client, [{"operation_id": "op-1", "answer": "yes"}])
    body = run_console(scenario, service, core)
    result = body["results"][0]
    assert result["ok"] is True and result["operation_id"] == "op-1" and result["paid"] is True
    assert result["text"] == "已處理 op-1" and result["still_pending"] is False and result["kind"] == "paid"
    call = core.calls[0]
    assert call["actor_id"] == OWNER_ID and call["service"] is service
    assert call["answer"] is FakeAnswer.YES and call["duplicate_of_sequence"] is None
    assert isinstance(call["tokens"], dict)


def test_request_cannot_choose_the_acting_owner() -> None:
    core = FakeCore()

    async def scenario(client) -> None:
        payload = {"decisions": [{"operation_id": "op-1", "answer": "yes", "actor_id": 1, "owner_id": 1}], "actor_id": 1}
        await client.post("/api/decide", json=payload, headers=authed())
    run_console(scenario, standard_service(), core)
    assert [call["actor_id"] for call in core.calls] == [OWNER_ID]


def test_decision_runs_outside_the_event_loop_thread() -> None:
    core = FakeCore()
    loop_thread: list[int] = []

    async def scenario(client) -> None:
        loop_thread.append(threading.get_ident())
        await post_decisions(client, [{"operation_id": "op-1", "answer": "yes"}])
    run_console(scenario, standard_service(), core)
    assert core.threads and core.threads[0] != loop_thread[0]


def test_batch_runs_in_order_and_one_failure_does_not_stop_the_rest() -> None:
    core = FakeCore(failing_operations=frozenset({"op-2"}))

    async def scenario(client) -> dict:
        decisions = [{"operation_id": "op-1", "answer": "yes"}, {"operation_id": "op-2", "answer": "yes"},
                     {"operation_id": "op-3", "answer": "no"}]
        return await post_decisions(client, decisions)
    body = run_console(scenario, standard_service(), core)
    assert [call["operation_id"] for call in core.calls] == ["op-1", "op-2", "op-3"]
    assert [result["ok"] for result in body["results"]] == [True, False, True]
    failed = body["results"][1]
    assert failed["error_class"] == "RuntimeError" and "database-password-leak" not in str(body)
    assert failed["text"]


def test_needs_duration_and_frozen_cases_are_refused_without_calling_the_core() -> None:
    core = FakeCore({"op-1": make_choices(needs_duration=True, can_yes=False, can_no=False),
                     "op-2": make_choices(frozen_conflict=True, can_yes=False, can_no=False)})

    async def scenario(client) -> dict:
        return await post_decisions(client, [{"operation_id": "op-1", "answer": "yes"}, {"operation_id": "op-2", "answer": "no"}])
    body = run_console(scenario, standard_service(), core)
    assert core.calls == []
    assert [result["ok"] for result in body["results"]] == [False, False]
    assert all(result["text"] for result in body["results"])


def test_answer_the_core_does_not_offer_is_refused() -> None:
    core = FakeCore({"op-1": make_choices(can_no=False)})

    async def scenario(client) -> dict:
        return await post_decisions(client, [{"operation_id": "op-1", "answer": "no"}])
    body = run_console(scenario, standard_service(), core)
    assert core.calls == [] and body["results"][0]["ok"] is False


def test_no_on_approved_case_requires_naming_the_duplicate() -> None:
    core = FakeCore({"op-1": make_choices(no_requires_duplicate_pick=True)})

    async def scenario(client) -> dict:
        return await post_decisions(client, [{"operation_id": "op-1", "answer": "no"}])
    body = run_console(scenario, standard_service(), core)
    assert core.calls == [] and body["results"][0]["ok"] is False


def test_no_with_chosen_duplicate_passes_the_sequence_to_the_core() -> None:
    core = FakeCore({"op-1": make_choices(no_requires_duplicate_pick=True)})

    async def scenario(client) -> dict:
        return await post_decisions(client, [{"operation_id": "op-1", "answer": "no", "duplicate_of_sequence": 9}])
    body = run_console(scenario, standard_service(), core)
    assert body["results"][0]["ok"] is True
    assert core.calls[0]["duplicate_of_sequence"] == 9 and core.calls[0]["answer"] is FakeAnswer.NO


def test_case_no_longer_pending_is_reported_not_raised() -> None:
    core = FakeCore()

    async def scenario(client) -> dict:
        return await post_decisions(client, [{"operation_id": "op-gone", "answer": "yes"}, {"operation_id": "op-1", "answer": "yes"}])
    body = run_console(scenario, standard_service(), core)
    assert [result["ok"] for result in body["results"]] == [False, True]
    assert body["results"][0]["error_class"] == "NotPending"
    assert [call["operation_id"] for call in core.calls] == ["op-1"]


def test_malformed_decision_requests_are_rejected_before_any_decision() -> None:
    core = FakeCore()
    bad_bodies = [
        {"decisions": []},
        {"decisions": "nope"},
        {"decisions": [{"operation_id": "op-1", "answer": "maybe"}]},
        {"decisions": [{"operation_id": "", "answer": "yes"}]},
        {"decisions": [{"operation_id": 5, "answer": "yes"}]},
        {"decisions": [{"operation_id": "op-1", "answer": "yes"}, {"operation_id": "op-1", "answer": "no"}]},
        {"decisions": [{"operation_id": "op-1", "answer": "no", "duplicate_of_sequence": True}]},
        {"decisions": [{"operation_id": "op-1", "answer": "no", "duplicate_of_sequence": "9"}]},
        {"decisions": [{"operation_id": f"op-{number}", "answer": "yes"} for number in range(1000)]},
        ["not", "an", "object"],
    ]

    async def scenario(client) -> list[int]:
        statuses = []
        for body in bad_bodies:
            response = await client.post("/api/decide", json=body, headers=authed())
            statuses.append(response.status)
        invalid = await client.post("/api/decide", data=b"{broken", headers=authed({"Content-Type": "application/json"}))
        return statuses + [invalid.status]
    statuses = run_console(scenario, standard_service(), core)
    assert statuses == [400] * (len(bad_bodies) + 1)
    assert core.calls == []


def test_listing_failure_returns_fixed_error_and_a_reload_works() -> None:
    service = standard_service()

    async def scenario(client) -> tuple[int, dict, dict]:
        service.fail = True
        failed = await client.get("/api/cases", headers=authed())
        failed_body = await failed.json()
        service.fail = False
        return failed.status, failed_body, await fetch_cases(client)
    status, failed_body, recovered = run_console(scenario, service)
    assert status == 500 and failed_body["error"] == "RuntimeError" and failed_body["message"]
    assert "db-host.internal" not in str(failed_body)
    assert len(recovered["cases"]) == 4


def test_decision_endpoint_reports_listing_failure_without_running_anything() -> None:
    service = standard_service()
    service.fail = True
    core = FakeCore()

    async def scenario(client) -> int:
        response = await client.post("/api/decide", json={"decisions": [{"operation_id": "op-1", "answer": "yes"}]}, headers=authed())
        return response.status
    assert run_console(scenario, service, core) == 500
    assert core.calls == []


def test_page_assets_use_plain_wording_without_forbidden_terms() -> None:
    from bot.owner_web_page import APP_CSS, APP_JS, INDEX_HTML
    for text in (APP_CSS, APP_JS, INDEX_HTML):
        assert "錄音版本" not in text and "版本" not in text


def test_page_offers_filters_bulk_selection_submit_and_reload() -> None:
    from bot.owner_web_page import APP_JS, INDEX_HTML
    combined = INDEX_HTML + APP_JS
    for label in ("全部", "有疑慮", "等時長", "等確認是否重複", "凍結衝突", "全選", "全部選是", "全部選不是", "送出", "重新載入"):
        assert label in combined


EXCHANGE_ROW = {"operation_code": 7, "requester": 555, "delivery_state": "delivered", "created_at": "2026-11-03T10:00:00+00:00",
                "refund_state": "not_refunded", "reason": "", "waiting_seconds": 12,
                "link": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "token": "secret-token", "balance": 99}


def exchange_lookup_for(row: dict | None, calls: list | None = None):
    """@description: Build a fake repository lookup returning a scripted row and recording its arguments.
    @param row: Row to return, or None for not found.
    @param calls: List receiving (requester, code) tuples.
    @return: Lookup callable.
    """
    def lookup(requester_id, operation_code):
        if calls is not None:
            calls.append((requester_id, operation_code))
        return row
    return lookup


def get_exchange(code: str, lookup, expected_status: int = 200):
    """@description: Query /api/exchange with the access token.
    @param code: Raw code text.
    @param lookup: Fake lookup.
    @param expected_status: Status expected.
    @return: Parsed JSON body.
    """
    async def scenario(client):
        response = await client.get("/api/exchange", params={"code": code}, headers=authed())
        assert response.status == expected_status
        return await response.json()
    return run_console(scenario, standard_service(), exchange_lookup=lookup)


def test_exchange_lookup_returns_only_whitelisted_fields_for_the_configured_owner() -> None:
    calls: list = []
    body = get_exchange("7", exchange_lookup_for(EXCHANGE_ROW, calls))
    assert calls == [(OWNER_ID, 7)]
    assert body["found"] is True
    exchange = body["exchange"]
    assert exchange["operation_code"] == 7 and exchange["requester"] == 555 and exchange["waiting_seconds"] == 12
    assert exchange["delivery_state"] == "delivered" and exchange["state_text"] == "已送達"
    for forbidden in ("link", "token", "balance"):
        assert forbidden not in exchange
    assert "youtube.com" not in str(body) and "secret-token" not in str(body)


def test_exchange_lookup_not_found_is_a_fixed_answer() -> None:
    body = get_exchange("8", exchange_lookup_for(None))
    assert body["found"] is False and body["message"]


def test_exchange_lookup_rejects_non_positive_integers_without_calling_the_repository() -> None:
    calls: list = []
    for code in ("abc", "", "0", "-3", "1.5", " 7", "7; DROP", "１２", "9" * 40):
        assert get_exchange(code, exchange_lookup_for(EXCHANGE_ROW, calls), 400)["message"]
    assert calls == []


def test_exchange_lookup_requires_the_token_and_a_code() -> None:
    async def scenario(client):
        return [(await client.get("/api/exchange", params={"code": "7"})).status, (await client.get("/api/exchange", headers=authed())).status]
    assert run_console(scenario, standard_service(), exchange_lookup=exchange_lookup_for(EXCHANGE_ROW)) == [401, 400]


def test_exchange_lookup_failure_returns_the_class_only() -> None:
    def denied(requester_id, operation_code):
        raise PermissionError("host=db.internal password=hunter2")
    async def scenario(client):
        response = await client.get("/api/exchange", params={"code": "7"}, headers=authed())
        return response.status, await response.text()
    status, text = run_console(scenario, standard_service(), exchange_lookup=denied)
    assert status == 500 and "PermissionError" in text and "hunter2" not in text


def test_page_has_the_exchange_search_box() -> None:
    from bot.owner_web_page import APP_JS, INDEX_HTML
    assert "查兌換問題" in INDEX_HTML and "/api/exchange" in APP_JS


def test_exchange_states_refunds_and_reasons_use_plain_wording() -> None:
    expected_states = {"reserved": "已預約", "sending": "傳送中", "delivered": "已送達", "failed": "失敗", "uncertain": "結果不明"}
    for stored, text in expected_states.items():
        row = {**EXCHANGE_ROW, "delivery_state": stored}
        assert get_exchange("7", exchange_lookup_for(row))["exchange"]["state_text"] == text
    refunds = {"refunded": "已退點", "refund_pending": "退點處理中", "not_needed": "不需退點"}
    for stored, text in refunds.items():
        row = {**EXCHANGE_ROW, "refund_state": stored}
        assert get_exchange("7", exchange_lookup_for(row))["exchange"]["refund_text"] == text
    for reason in ("delivery_result_unknown", "refund_pending", "sending_lease_expired", "reserved_not_sent"):
        row = {**EXCHANGE_ROW, "reason": reason}
        assert get_exchange("7", exchange_lookup_for(row))["exchange"]["reason_text"]
    assert get_exchange("7", exchange_lookup_for({**EXCHANGE_ROW, "reason": None}))["exchange"]["reason_text"] == ""
