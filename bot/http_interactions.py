"""Route verified HTTP interaction payloads to the existing services and answer each one inside Discord's acknowledgement window.

Commands acknowledge with a deferred private response, then run as tracked background work that edits the original response; the Gateway client is never used.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Coroutine, Mapping
from functools import partial
from typing import Any

import aiohttp
from aiohttp import web
from nacl.signing import VerifyKey

from bot.discord_http import (DiscordRestClient, DiscordRestError, RestDirectMessageSender, RestEphemeralReplier, verify_interaction_signature)
from bot.http_config import HttpInteractionSettings
from bot.services import MusicService, ServiceUnavailable
from bot.urls import InvalidVideoUrl

LOGGER = logging.getLogger(__name__)
INTERACTIONS_PATH = "/interactions"
SESSION_KEY = web.AppKey("session", aiohttp.ClientSession)  # [application state]: Shared REST session, closed after drain.
VERIFY_KEY = web.AppKey("verify_key", VerifyKey)  # [application state]: Public key used for every request signature.
MAX_BODY_BYTES = 64 * 1024  # [request bound]: Interaction payloads are small; larger bodies are refused before parsing.
INTERACTION_PING = 1
INTERACTION_APPLICATION_COMMAND = 2
RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4
RESPONSE_DEFERRED_CHANNEL_MESSAGE = 5
EPHEMERAL_FLAG = 64  # [private reply]: The only flag allowed on a deferred response.
REFUSAL_TEXT = "此指令不可用。"  # [admission refusal]: Identical for wrong guild, DM and wrong application, so it reveals nothing.
GENERIC_FAILURE_TEXT = "指令暫時無法完成，請稍後再試。"  # [reviewed wording]: Same as the Gateway tree's generic failure reply.
MISSING_URL_TEXT = "請提供要投稿的 YouTube 影片網址。"
DAILY_CHOICES = frozenset({"on", "off"})
DAILY_CHOICE_TEXT = "每日推薦設定只接受 on 或 off。"  # [reviewed wording]: Same as the Gateway handler.
DRAIN_TIMEOUT_SECONDS = 25.0  # [shutdown bound]: Above the 20-second DM attempt; the service stop timeout must exceed it.


class BackgroundWork:
    """Track fire-and-forget interaction work so failures are logged by class and shutdown waits a bounded time."""

    def __init__(self) -> None:
        """@description: Start with no tracked work.
        @param self: Tracker instance.
        @return: None.
        """
        self._tasks: set[asyncio.Task] = set()  # [live work]: Each task stays here until it finishes or is drained.

    def start(self, work: Awaitable[None]) -> None:
        """@description: Run one awaitable in the event loop and keep a reference until it ends.
        @param self: Tracker instance.
        @param work: Awaitable whose failure is logged by class name only.
        @return: None.
        """
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._finish)

    def _finish(self, task: asyncio.Task) -> None:
        """@description: Release a finished task and log any failure by class name, never by message.
        @param self: Tracker instance.
        @param task: Completed or cancelled task.
        @return: None.
        """
        self._tasks.discard(task)
        if task.cancelled():
            LOGGER.warning("Interaction work cancelled before completion")
            return
        error = task.exception()
        if error is not None:
            LOGGER.error("Interaction work failed: %s", type(error).__name__)

    async def drain(self, timeout_seconds: float = DRAIN_TIMEOUT_SECONDS) -> int:
        """@description: Wait a bounded time for tracked work at shutdown, then cancel what remains so no task outlives the process.
        @param self: Tracker instance.
        @param timeout_seconds: Maximum wait before cancellation.
        @return: Number of tasks cancelled because they did not finish in time.
        """
        if not self._tasks:
            return 0
        _, pending = await asyncio.wait(set(self._tasks), timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        if pending:
            LOGGER.warning("Cancelled %d unfinished interaction tasks at shutdown", len(pending))
            await asyncio.gather(*pending, return_exceptions=True)
        return len(pending)


class InteractionDispatcher:
    """Admit, parse and route one verified interaction payload to the existing service without a Gateway client."""

    def __init__(self, service: MusicService, rest: DiscordRestClient, *, guild_id: int, application_id: int,
                 background: BackgroundWork) -> None:
        """@description: Inject the service, REST client and admission identifiers so the dispatcher stays independent of transport.
        @param self: Dispatcher instance.
        @param service: Existing application service.
        @param rest: REST client for replies and direct messages.
        @param guild_id: The single private guild admitted.
        @param application_id: The single application admitted.
        @param background: Tracker for deferred command work.
        @return: None.
        """
        self._service = service
        self._rest = rest
        self._guild_id = str(guild_id)  # [admission]: Discord sends snowflakes as strings.
        self._application_id = str(application_id)
        self._application_numeric_id = application_id
        self._background = background

    async def dispatch(self, payload: Mapping[str, Any]) -> tuple[dict, Awaitable[None] | None]:
        """@description: Decide the synchronous response and return any deferred work without starting it, so no side effect precedes the response.
        @param self: Dispatcher instance.
        @param payload: Parsed interaction object whose signature has already been verified.
        @return: Response body for Discord, and the awaitable to start after that body is handed to the transport, or None.
        """
        if payload.get("type") == INTERACTION_PING:
            return {"type": RESPONSE_PONG}, None
        if payload.get("type") != INTERACTION_APPLICATION_COMMAND or not self._admitted(payload):
            return _ephemeral_message(REFUSAL_TEXT), None
        return self._acknowledge(payload)

    def start_work(self, work: Awaitable[None] | None) -> None:
        """@description: Start deferred work after its acknowledgement has been written, so state changes never precede the response.
        @param self: Dispatcher instance.
        @param work: Deferred awaitable from dispatch, or None when nothing is deferred.
        @return: None.
        """
        if work is not None:
            self._background.start(work)

    async def drain(self) -> int:
        """@description: Let tracked command work finish within the shutdown bound.
        @param self: Dispatcher instance.
        @return: Number of tasks cancelled at the bound.
        """
        return await self._background.drain()

    def _admitted(self, payload: Mapping[str, Any]) -> bool:
        """@description: Admit only commands from the configured guild and application that carry a guild member.
        @param self: Dispatcher instance.
        @param payload: Parsed interaction object.
        @return: True only when guild, application and member all match the private scope.
        """
        return (payload.get("guild_id") == self._guild_id and payload.get("application_id") == self._application_id
                and "member" in payload)

    def _acknowledge(self, payload: Mapping[str, Any]) -> tuple[dict, Awaitable[None] | None]:
        """@description: Build the deferred private response and hand back its work unstarted, or refuse an unknown command.
        @param self: Dispatcher instance.
        @param payload: Admitted application-command payload.
        @return: Deferred response body with its work, or a refusal with no work.
        """
        work = self._command_work(payload)
        if work is None:
            return _ephemeral_message(REFUSAL_TEXT), None
        return {"type": RESPONSE_DEFERRED_CHANNEL_MESSAGE, "data": {"flags": EPHEMERAL_FLAG}}, work

    def _command_work(self, payload: Mapping[str, Any]) -> Coroutine[Any, Any, None] | None:
        """@description: Map a command name and its options to one awaitable that finishes the interaction.
        @param self: Dispatcher instance.
        @param payload: Admitted application-command payload.
        @return: Awaitable completing the interaction, or None for an unknown command.
        """
        command_name = payload.get("data", {}).get("name")
        user_id = int(payload["member"]["user"]["id"])
        token = payload["token"]
        request_key = f"discord:{payload['id']}"  # [same key as Gateway]: Preserves idempotency across transports.
        options = _options_by_name(payload)
        if command_name == "share":
            url = options.get("url")
            if not isinstance(url, str):
                return self._complete(token, _immediate(MISSING_URL_TEXT))
            return self._complete(token, self._service.share(user_id, url, request_key))
        if command_name == "balance":
            return self._complete(token, self._service.balance(user_id))
        if command_name == "history":
            return self._complete(token, self._service.history(user_id))
        if command_name == "daily":
            return self._daily_work(token, user_id, options.get("mode"))
        if command_name == "exchange":
            return self._exchange_work(token, user_id, request_key)
        return None

    def _daily_work(self, token: str, user_id: int, mode: Any) -> Coroutine[Any, Any, None]:
        """@description: Persist an explicit on or off preference and refuse any other choice without guessing.
        @param self: Dispatcher instance.
        @param token: Interaction token for the private reply.
        @param user_id: Invoking user.
        @param mode: Submitted option value.
        @return: Awaitable completing the interaction.
        """
        if mode not in DAILY_CHOICES:
            return self._complete(token, _immediate(DAILY_CHOICE_TEXT))
        return self._complete(token, self._service.daily(user_id, mode == "on"))

    def _exchange_work(self, token: str, user_id: int, request_key: str) -> Coroutine[Any, Any, None]:
        """@description: Route /exchange to the formal flow only when its switch is exactly on; otherwise keep the fixed closed reply.
        @param self: Dispatcher instance.
        @param token: Interaction token for the private reply.
        @param user_id: Requesting user.
        @param request_key: Request identity shared with the Gateway path.
        @return: Awaitable completing the interaction.
        """
        if self._service.formal_exchange_enabled is True:
            return self._formal_exchange(token, user_id, request_key)
        return self._complete(token, self._service.exchange(user_id))

    async def _formal_exchange(self, token: str, user_id: int, request_key: str) -> None:
        """@description: Run the formal exchange with REST delivery; its own replier edits the reply, so nothing is edited here.
        @param self: Dispatcher instance.
        @param token: Interaction token used only by the replier.
        @param user_id: Requesting user.
        @param request_key: Request identity shared with the Gateway path.
        @return: None.
        """
        sender = RestDirectMessageSender(self._rest)
        replier = RestEphemeralReplier(self._rest, self._application_numeric_id, token)
        await self._complete_flow(token, self._service.formal_exchange(user_id, request_key, sender, replier))

    async def _complete_flow(self, token: str, operation: Awaitable[Any]) -> None:
        """@description: Await a flow that replies itself, and report only an unexpected failure by class name.
        @param self: Dispatcher instance.
        @param token: Interaction token for the generic failure reply.
        @param operation: Flow awaitable that replies on its own.
        @return: None.
        """
        try:
            await operation
        except Exception as error:  # [command boundary]: Log the class only; the reply is generic.
            LOGGER.error("Exchange command failed: %s", type(error).__name__)
            try:
                await self._rest.edit_original_response(self._application_numeric_id, token, GENERIC_FAILURE_TEXT)
            except DiscordRestError as reply_error:  # [reply not confirmed]: Logged by reason only and never repeated, so no duplicate reply is sent.
                LOGGER.error("Interaction reply not confirmed: %s", reply_error.reason)

    async def _complete(self, token: str, operation: Awaitable[str]) -> None:
        """@description: Await a service operation and replace the deferred response with its text, mapping expected errors to their own wording.
        @param self: Dispatcher instance.
        @param token: Interaction token for the private reply.
        @param operation: Service awaitable returning user-visible text.
        @return: None.
        """
        try:
            text = await operation
        except (InvalidVideoUrl, ServiceUnavailable) as expected_error:
            text = str(expected_error)
        except Exception as error:  # [command boundary]: Log the class only; never the arguments.
            LOGGER.error("Interaction command failed: %s", type(error).__name__)
            text = GENERIC_FAILURE_TEXT
        try:
            await self._rest.edit_original_response(self._application_numeric_id, token, text)
        except DiscordRestError as error:  # [reply not confirmed]: Logged by reason only and never repeated, so no duplicate reply is sent.
            LOGGER.error("Interaction reply not confirmed: %s", error.reason)


async def _immediate(text: str) -> str:
    """@description: Wrap a constant reply as an awaitable so validation refusals share the deferred path.
    @param text: Constant reply text.
    @return: The same text.
    """
    return text


def _ephemeral_message(text: str) -> dict:
    """@description: Build a private channel-message response for refusals that need no deferral.
    @param text: Link-free reply text.
    @return: Interaction response body.
    """
    return {"type": RESPONSE_CHANNEL_MESSAGE, "data": {"content": text, "flags": EPHEMERAL_FLAG}}


def _options_by_name(payload: Mapping[str, Any]) -> dict:
    """@description: Index top-level slash-command options by name so handlers read values by their registered names.
    @param payload: Application-command payload.
    @return: Mapping from option name to its submitted value.
    """
    options = payload.get("data", {}).get("options", [])
    return {option["name"]: option.get("value") for option in options}


DISPATCHER_KEY = web.AppKey("dispatcher", InteractionDispatcher)  # [application state]: Single dispatcher per process, built at startup.


async def _handle_interaction(request: web.Request) -> web.Response:
    """@description: Verify the raw body before parsing, then answer with the dispatcher's response body.
    @param request: Incoming HTTPS-proxied request.
    @return: Interaction response, or 401 for a failed signature and 400 for an unparsable body.
    """
    raw_body = await request.read()
    if not verify_interaction_signature(request.app[VERIFY_KEY], request.headers, raw_body):
        return web.Response(status=401)  # [before parsing]: An unverified body never reaches the parser or business code.
    try:
        payload = json.loads(raw_body)
    except ValueError:
        return web.Response(status=400)
    if not isinstance(payload, dict):
        return web.Response(status=400)
    dispatcher = request.app[DISPATCHER_KEY]
    body, work = await dispatcher.dispatch(payload)
    response = web.json_response(body)
    await response.prepare(request)
    await response.write_eof()  # [handed to transport]: Deferred work starts only after the acknowledgement bytes are written.
    dispatcher.start_work(work)  # [no early side effect]: If the write fails, nothing was reserved, debited or sent.
    return response


async def _open_resources(app: web.Application, *, service: MusicService, settings: HttpInteractionSettings) -> None:
    """@description: Create the REST session and dispatcher inside the running event loop, never at import time.
    @param app: Application being started.
    @param service: Existing application service.
    @param settings: Validated HTTP interaction settings.
    @return: None.
    """
    session = aiohttp.ClientSession()
    app[SESSION_KEY] = session
    app[VERIFY_KEY] = settings.verify_key
    rest = DiscordRestClient(session, settings.bot_token)
    app[DISPATCHER_KEY] = InteractionDispatcher(service, rest, guild_id=settings.guild_id,
                                                application_id=settings.application_id, background=BackgroundWork())


async def _drain_work(app: web.Application) -> None:
    """@description: Finish tracked work within the shutdown bound before connections close.
    @param app: Application being shut down.
    @return: None.
    """
    await app[DISPATCHER_KEY].drain()


async def _close_session(app: web.Application) -> None:
    """@description: Close the shared REST session after work has drained.
    @param app: Application being cleaned up.
    @return: None.
    """
    await app[SESSION_KEY].close()


def build_app(service: MusicService, settings: HttpInteractionSettings) -> web.Application:
    """@description: Assemble the single HTTP endpoint whose REST session and tracked background work are owned by the application lifecycle.
    @param service: Existing application service, built once per process.
    @param settings: Validated HTTP interaction settings.
    @return: Configured aiohttp application.
    """
    app = web.Application(client_max_size=MAX_BODY_BYTES)
    app.router.add_post(INTERACTIONS_PATH, _handle_interaction)
    app.on_startup.append(partial(_open_resources, service=service, settings=settings))
    app.on_shutdown.append(_drain_work)
    app.on_cleanup.append(_close_session)
    return app
