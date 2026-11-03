"""Offline tests for the Owner web console's loopback-only, token-guarded and hardened HTTP surface.

Everything runs against a test server on a loopback port with fake collaborators; no database, Discord or browser is used.
"""

import asyncio
import logging
import re

import aiohttp
import pytest

from bot import owner_web
from bot.owner_web import (IDLE_TIMEOUT_SECONDS, MAX_BODY_BYTES, TOKEN_HEADER, ConsoleRefused, IdleMonitor, SlidingWindowLimiter, build_console_app,
                           new_access_token, serve_console, validate_bind_host)
from tests.test_owner_web_api import (ACCESS_TOKEN, OWNER_ID, FakeCore, authed, free_port, run_console, standard_service)

REQUIRED_HEADERS = ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "Cache-Control")


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert validate_bind_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "", "::", "localhost", "192.168.1.5", "127.0.0.2", "10.0.0.1", " 127.0.0.1", "*"])
def test_every_other_bind_address_is_refused(host: str) -> None:
    with pytest.raises(ConsoleRefused):
        validate_bind_host(host)


def test_main_refuses_a_public_address_before_reading_any_setting(monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise AssertionError("settings must not be read before the bind check")
    monkeypatch.setattr(owner_web, "load_console_dependencies", explode, raising=False)
    with pytest.raises(SystemExit):
        owner_web.main(["--host", "0.0.0.0", "--port", "8765"])


@pytest.mark.parametrize("port", ["0", "-1", "70000", "abc"])
def test_main_refuses_invalid_ports(port: str) -> None:
    with pytest.raises(SystemExit):
        owner_web.main(["--port", port])


def test_serve_console_refuses_a_public_address() -> None:
    app = build_console_app(standard_service(), OWNER_ID, ACCESS_TOKEN, 8765, core=FakeCore())
    with pytest.raises(ConsoleRefused):
        asyncio.run(serve_console(app, "0.0.0.0", 8765, asyncio.Event()))


def test_access_token_is_long_random_and_url_safe() -> None:
    first, second = new_access_token(), new_access_token()
    assert first != second and len(first) >= 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", first)


def test_api_requires_the_correct_token() -> None:
    async def scenario(client) -> list[int]:
        statuses = [(await client.get("/api/cases")).status]
        statuses.append((await client.get("/api/cases", headers={TOKEN_HEADER: "wrong"})).status)
        statuses.append((await client.get("/api/cases", headers={TOKEN_HEADER: ""})).status)
        statuses.append((await client.get("/api/cases", headers=authed())).status)
        return statuses
    assert run_console(scenario, standard_service()) == [401, 401, 401, 200]


def test_token_in_the_query_string_is_not_accepted() -> None:
    async def scenario(client) -> int:
        return (await client.get(f"/api/cases?token={ACCESS_TOKEN}")).status
    assert run_console(scenario, standard_service()) == 401


def test_token_comparison_is_constant_time(monkeypatch) -> None:
    calls: list[tuple] = []
    real = owner_web.secrets.compare_digest
    monkeypatch.setattr(owner_web.secrets, "compare_digest", lambda left, right: calls.append((left, right)) or real(left, right))

    async def scenario(client) -> None:
        await client.get("/api/cases", headers=authed())
    run_console(scenario, standard_service())
    assert calls


def test_post_without_token_is_refused_and_nothing_runs() -> None:
    core = FakeCore()

    async def scenario(client) -> int:
        body = {"decisions": [{"operation_id": "op-1", "answer": "yes"}]}
        return (await client.post("/api/decide", json=body)).status
    assert run_console(scenario, standard_service(), core) == 401
    assert core.calls == []


def test_state_changing_endpoint_is_post_only_and_reads_are_get_only() -> None:
    async def scenario(client) -> list[int]:
        return [(await client.get("/api/decide", headers=authed())).status, (await client.put("/api/decide", headers=authed())).status,
                (await client.post("/api/cases", json={}, headers=authed())).status, (await client.delete("/api/cases", headers=authed())).status]
    assert run_console(scenario, standard_service()) == [405, 405, 405, 405]


def test_post_requires_json_content_type() -> None:
    async def scenario(client) -> int:
        return (await client.post("/api/decide", data="decisions=1", headers=authed({"Content-Type": "text/plain"}))).status
    assert run_console(scenario, standard_service()) == 415


def test_oversized_body_is_refused() -> None:
    async def scenario(client) -> int:
        payload = b'{"decisions":"' + b"x" * (MAX_BODY_BYTES + 10) + b'"}'
        return (await client.post("/api/decide", data=payload, headers=authed({"Content-Type": "application/json"}))).status
    assert run_console(scenario, standard_service()) == 413


def test_host_header_must_be_loopback_with_the_serving_port() -> None:
    port_holder: list[int] = []

    async def scenario(client) -> list[int]:
        port = client.port
        port_holder.append(port)
        hosts = [f"evil.example:{port}", "evil.example", "127.0.0.1", f"127.0.0.1:{port + 1}", f"localhost:{port + 1}", "", f"127.0.0.1.evil.example:{port}"]
        statuses = [(await client.get("/api/cases", headers=authed({"Host": host}))).status for host in hosts]
        statuses.append((await client.get("/", headers={"Host": f"evil.example:{port}"})).status)
        return statuses
    statuses = run_console(scenario, standard_service())
    assert all(status in (400, 403) for status in statuses)


def test_localhost_and_loopback_literal_hosts_are_accepted() -> None:
    async def scenario(client) -> list[int]:
        port = client.port
        return [(await client.get("/api/cases", headers=authed({"Host": host}))).status for host in (f"127.0.0.1:{port}", f"localhost:{port}")]
    assert run_console(scenario, standard_service()) == [200, 200]


def test_origin_must_match_when_present() -> None:
    async def scenario(client) -> list[int]:
        port = client.port
        origins = ["http://evil.example", f"http://evil.example:{port}", "null", f"https://127.0.0.1:{port}", f"http://127.0.0.1:{port + 1}"]
        statuses = [(await client.get("/api/cases", headers=authed({"Origin": origin}))).status for origin in origins]
        statuses.append((await client.get("/api/cases", headers=authed({"Origin": f"http://127.0.0.1:{port}"}))).status)
        statuses.append((await client.get("/api/cases", headers=authed({"Origin": f"http://localhost:{port}", "Host": f"localhost:{port}"}))).status)
        return statuses
    assert run_console(scenario, standard_service()) == [403, 403, 403, 403, 403, 200, 200]


def test_cross_origin_post_cannot_run_a_decision() -> None:
    core = FakeCore()

    async def scenario(client) -> int:
        body = {"decisions": [{"operation_id": "op-1", "answer": "yes"}]}
        return (await client.post("/api/decide", json=body, headers=authed({"Origin": "http://evil.example"}))).status
    assert run_console(scenario, standard_service(), core) == 403
    assert core.calls == []


def test_strict_security_headers_on_page_api_and_errors() -> None:
    async def scenario(client) -> list:
        responses = [await client.get("/"), await client.get("/app.js"), await client.get("/app.css"), await client.get("/api/cases", headers=authed()),
                     await client.get("/api/cases"), await client.get("/missing")]
        return [dict(response.headers) for response in responses]
    for headers in run_console(scenario, standard_service()):
        for name in REQUIRED_HEADERS:
            assert name in headers, name
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert "no-store" in headers["Cache-Control"]
        policy = headers["Content-Security-Policy"]
        for required in ("default-src 'none'", "script-src 'self'", "style-src 'self'", "img-src https://i.ytimg.com data:", "connect-src 'self'",
                         "frame-ancestors 'none'"):
            assert required in policy, required
        assert "unsafe-inline" not in policy and "unsafe-eval" not in policy


def test_page_has_no_inline_script_style_or_external_reference() -> None:
    from bot.owner_web_page import APP_CSS, APP_JS, INDEX_HTML
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", INDEX_HTML) and not re.search(r"<style", INDEX_HTML) and "style=" not in INDEX_HTML
    assert not re.search(r"\son[a-z]+\s*=", INDEX_HTML)
    for text in (INDEX_HTML, APP_JS, APP_CSS):
        for address in re.findall(r"https?://[^\s'\"`)<>]+", text):
            assert address.startswith("https://i.ytimg.com") or address.startswith("https://www.youtube.com/"), address
    assert "cdn" not in (INDEX_HTML + APP_JS + APP_CSS).lower()


def test_page_script_never_writes_untrusted_text_as_html() -> None:
    from bot.owner_web_page import APP_JS
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "localStorage", "sessionStorage"):
        assert forbidden not in APP_JS, forbidden


def test_page_keeps_the_token_out_of_urls_and_sends_it_in_the_custom_header() -> None:
    from bot.owner_web_page import APP_JS
    assert "location.hash" in APP_JS and TOKEN_HEADER in APP_JS
    assert "history.replaceState" in APP_JS
    assert not re.search(r"\?token=|[&?]t=", APP_JS)


def test_page_is_served_without_a_token_but_carries_no_data() -> None:
    async def scenario(client) -> tuple[int, str]:
        response = await client.get("/")
        return response.status, await response.text()
    status, text = run_console(scenario, standard_service())
    assert status == 200 and ACCESS_TOKEN not in text and "<!doctype html>" in text.lower()


def test_errors_return_fixed_sentences_and_the_class_only() -> None:
    service = standard_service()
    service.fail = True

    async def scenario(client) -> tuple[int, str]:
        response = await client.get("/api/cases", headers=authed())
        return response.status, await response.text()
    status, text = run_console(scenario, service)
    assert status == 500 and "RuntimeError" in text
    for leaked in ("db-host.internal", "Traceback", "File \"", ACCESS_TOKEN):
        assert leaked not in text


def test_unknown_route_returns_the_fixed_json_error() -> None:
    async def scenario(client) -> tuple[int, dict]:
        response = await client.get("/api/does-not-exist", headers=authed())
        return response.status, await response.json()
    status, body = run_console(scenario, standard_service())
    assert status == 404 and set(body) >= {"error", "message"}


def test_sliding_window_limiter_blocks_after_the_budget_and_recovers() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(3, 10.0, lambda: now[0])
    assert [limiter.allow() for _ in range(4)] == [True, True, True, False]
    now[0] = 11.0
    assert limiter.allow() is True


def test_request_rate_is_limited() -> None:
    async def scenario(client) -> list[int]:
        return [(await client.get("/api/cases", headers=authed())).status for _ in range(5)]
    statuses = run_console(scenario, standard_service(), rate_limit_requests=3)
    assert statuses[:3] == [200, 200, 200] and statuses[3:] == [429, 429]


def test_repeated_wrong_tokens_lock_out_even_the_right_token() -> None:
    async def scenario(client) -> list[int]:
        statuses = [(await client.get("/api/cases", headers={TOKEN_HEADER: "bad"})).status for _ in range(4)]
        statuses.append((await client.get("/api/cases", headers=authed())).status)
        return statuses
    statuses = run_console(scenario, standard_service(), max_failed_attempts=3)
    assert statuses == [401, 401, 401, 429, 429]


def test_idle_monitor_expires_after_the_timeout_and_touch_resets_it() -> None:
    now = [100.0]
    monitor = IdleMonitor(1800.0, lambda: now[0])
    now[0] += 1799.0
    assert monitor.is_expired() is False
    monitor.touch()
    now[0] += 1799.0
    assert monitor.is_expired() is False
    now[0] += 2.0
    assert monitor.is_expired() is True


def test_default_idle_timeout_is_thirty_minutes() -> None:
    assert IDLE_TIMEOUT_SECONDS == 30 * 60


def test_only_authenticated_requests_count_as_activity() -> None:
    now = [0.0]
    monitor = IdleMonitor(1800.0, lambda: now[0])

    async def scenario(client) -> None:
        now[0] = 100.0
        await client.get("/api/cases")
        assert monitor.is_expired() is False
        now[0] = 1801.0
        await client.get("/api/cases")
        assert monitor.is_expired() is True
        await client.get("/api/cases", headers=authed())
        assert monitor.is_expired() is False
    run_console(scenario, standard_service(), idle=monitor)


def test_serve_console_stops_by_itself_when_idle() -> None:
    now = [0.0]
    monitor = IdleMonitor(5.0, lambda: now[0], poll_seconds=0.01)
    port = free_port()
    app = build_console_app(standard_service(), OWNER_ID, ACCESS_TOKEN, port, core=FakeCore(), idle=monitor)

    async def go() -> bool:
        ready = asyncio.Event()
        task = asyncio.create_task(serve_console(app, "127.0.0.1", port, asyncio.Event(), ready, idle=monitor))
        await ready.wait()
        now[0] = 10.0
        await asyncio.wait_for(task, timeout=5)
        return task.done()
    assert asyncio.run(go()) is True


def test_serving_never_logs_the_token_or_request_query(caplog) -> None:
    port = free_port()
    app = build_console_app(standard_service(), OWNER_ID, ACCESS_TOKEN, port, core=FakeCore())

    async def go() -> None:
        stop, ready = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(serve_console(app, "127.0.0.1", port, stop, ready))
        await ready.wait()
        async with aiohttp.ClientSession() as session:
            await session.get(f"http://127.0.0.1:{port}/api/cases?token=query-secret-zzz", headers={TOKEN_HEADER: ACCESS_TOKEN})
            await session.get(f"http://127.0.0.1:{port}/api/cases", headers={TOKEN_HEADER: ACCESS_TOKEN})
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    with caplog.at_level(logging.DEBUG):
        asyncio.run(go())
    assert ACCESS_TOKEN not in caplog.text and "query-secret-zzz" not in caplog.text


def test_failures_are_logged_by_class_without_secrets(caplog) -> None:
    service = standard_service()
    service.fail = True

    async def scenario(client) -> None:
        await client.get("/api/cases", headers=authed())
    with caplog.at_level(logging.DEBUG):
        run_console(scenario, service)
    assert "db-host.internal" not in caplog.text and ACCESS_TOKEN not in caplog.text
