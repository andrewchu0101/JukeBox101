"""Run the labelled synthetic /exchange acceptance through the real Discord client, with scripted DM outcomes only.

Fixture-only entry under tests/: the synthetic world lives in this process's fake database and every DM is scripted.
No real direct message is sent and no real points are touched. Scripted modes are chosen at startup and cannot be changed
by a Discord user. Live DM sending is not part of this round, so --authorize-live-dm is refused explicitly.
"""

import argparse
import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock

from bot.acceptance_config import DevelopmentGuildSettings, environment_snowflake, load_development_guild_settings
from bot.acceptance_runtime import execute_acceptance
from bot.database.submission_repository import SubmissionRepository
from bot.discord_app import JukeBoxClient, OwnerReviewWiring
from bot.exchange_delivery import DmResult
from bot.gateway_listener import GuildInteractionGuard
from bot.gateway_service import GatewayAcceptanceService
from bot.owner_review import OwnerReviewService
from bot.services import SYNTHETIC_LABEL, MusicService
from tests.intake_fixture import seed_intake
from tests.owner_transport_acceptance import _utc_now, listen_owner_transport, synthetic_service
from tests.submission_persistence_fake import FakeDatabase
from tests.test_exchange_reservation import verify_all_songs
from tests.test_identity_service_path import (
    OWNER_ID,
    START,
    approve,
    confirm_version,
    owner_evidence,
    submission,
    video_of,
)

INJECTION_MODES = ("success", "not_sent", "uncertain", "refund_fail")  # [scripted outcomes]: Startup choice only; never a Discord option.
NOT_SENT_NOTICE = "未實際發送 DM（合成注入結果）。"  # [visible boundary]: Every reply in this entry says no DM was sent.
FIXED_TEST_LINK = "https://www.youtube.com/watch?v={video_id}"  # [reviewed fixed pattern]: Placeholder, not a verified playable video.
SYNTHETIC_NOTICE = "合成資料：案件只存在於此程序記憶體，不寫入 MySQL，不代表真實投稿，不動用真實點數，不實際發送 DM；連結為固定測試格式，不代表影片可播放。"
ORIGINAL_A = "synthetic-version-a"
ORIGINAL_B = "synthetic-version-b"


class InjectedDm:
    """Scripted direct-message outcome for this entry only; it never reaches Discord."""

    def __init__(self, mode: str) -> None:
        """@description: Select the scripted outcome for this run.
        @param self: Injected adapter.
        @param mode: Injection mode from the reviewed list.
        @return: None.
        """
        self._mode = mode

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Return the scripted classified outcome without any network call.
        @param self: Injected adapter.
        @param discord_user_id: Recipient, unused.
        @param text: Message body, unused.
        @return: Scripted result.
        """
        if self._mode == "uncertain":
            return DmResult.unknown("injected_uncertain")
        if self._mode in ("not_sent", "refund_fail"):
            return DmResult.not_sent("injected_not_sent")
        return DmResult.delivered(990001)


class _NoticeReplier:
    """Prefix every reply with the explicit statement that no DM was sent."""

    def __init__(self, inner) -> None:
        """@description: Wrap the ephemeral reply adapter.
        @param self: Notice wrapper.
        @param inner: Ephemeral reply adapter.
        @return: None.
        """
        self._inner = inner

    async def send_ephemeral(self, text: str) -> None:
        """@description: Send the reply with the no-DM notice in front.
        @param self: Notice wrapper.
        @param text: Reply body.
        @return: None.
        """
        await self._inner.send_ephemeral(NOT_SENT_NOTICE + text)


class SyntheticExchangeService:
    """Route /exchange to the labelled scripted flow and every other operation to the transport-only service."""

    def __init__(self, exchange: MusicService, database: FakeDatabase, injection: str) -> None:
        """@description: Bind the synthetic exchange service, the fake database for fault arming, and the startup-selected outcome.
        @param self: Composite service.
        @param exchange: Synthetic-enabled MusicService over the fake repository.
        @param database: Fake database whose next refund statement may be made to fail once.
        @param injection: Scripted outcome selected at startup.
        @return: None.
        """
        if injection not in INJECTION_MODES:
            raise ValueError("unknown injection mode")
        self._exchange = exchange
        self._database = database
        self._injection = injection
        self._transport = GatewayAcceptanceService()

    @property
    def synthetic_exchange_enabled(self) -> bool:
        """@description: Tell the handler the labelled synthetic route is active.
        @param self: Composite service.
        @return: True.
        """
        return True

    def exchange(self, discord_user_id: int) -> str:
        """@description: Keep the production gate text for any path that bypasses the synthetic route.
        @param self: Composite service.
        @param discord_user_id: Requester.
        @return: Gate text without debit.
        """
        return "交換功能待歌曲資格驗證與派送恢復流程完成後開放；本次未扣點。"

    async def synthetic_exchange(self, discord_user_id: int, request_key: str, sender, replier):
        """@description: Run one labelled exchange with the startup-selected scripted adapter; the handler's real adapter is ignored.
        @param self: Composite service.
        @param discord_user_id: Requester.
        @param request_key: Technical idempotency key.
        @param sender: Real adapter from the handler, deliberately unused here.
        @param replier: Ephemeral reply adapter.
        @return: Flow result labelled as synthetic with the no-DM notice.
        """
        del sender  # [no real DM]: The handler's adapter is never called in this entry.
        if self._injection == "refund_fail":
            self._database.sql_fault = "UPDATE recommendations SET released_at"  # [one refund failure]: Pending path only.
        flow = await self._exchange.synthetic_exchange(discord_user_id, request_key, InjectedDm(self._injection), _NoticeReplier(replier))
        return replace(flow, reply_text=NOT_SENT_NOTICE + flow.reply_text)  # [visible boundary]: The returned text states it too.

    def __getattr__(self, name: str):
        """@description: Delegate every other handler-facing operation to the transport-only service.
        @param self: Composite service.
        @param name: Operation name.
        @return: The transport operation.
        """
        return getattr(self._transport, name)


def build_world():
    """@description: Create the synthetic fake database: two settled users with different versions and one user with no points.
    @param: none
    @return: Tuple of fake database and synthetic repository.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)  # [fake clock]: Naive database time, as the fake stores it.
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: START, token_ttl=timedelta(minutes=5))
    for number, original in ((1, ORIGINAL_A), (2, ORIGINAL_B)):
        seed_intake(repository, submission(number))
        confirm_version(service, number, owner_evidence(video_of(number), f"ev-{number}", original))
        approve(service, number)
        repository.settle(f"op-{number}")
    seed_intake(repository, submission(3))  # [zero-point user]: Accepted but never settled, so it holds no points.
    verify_all_songs(database)
    for row in database.state["songs"].values():
        row.update(youtube_url=FIXED_TEST_LINK.format(video_id=row["platform_track_id"]), verification_provenance="synthetic-fixed-test-link")
    return database, repository


def build_exchange_service(injection: str) -> SyntheticExchangeService:
    """@description: Assemble the labelled scripted exchange service over the synthetic world.
    @param injection: Scripted outcome selected at startup.
    @return: Composite service for the Discord client.
    """
    database, repository = build_world()
    exchange = MusicService(MagicMock(), submission_repository=repository, synthetic_exchange=True)
    return SyntheticExchangeService(exchange, database, injection)


def build_exchange_client(settings: DevelopmentGuildSettings, owner_id: int, injection: str) -> JukeBoxClient:
    """@description: Assemble the real guarded client with the labelled exchange service and the Owner group, as the Owner transport does.
    @param settings: Explicit application and private guild targets.
    @param owner_id: Configured Owner Discord identity.
    @param injection: Scripted outcome selected at startup.
    @return: Client whose command tree admits only the configured guild and application.
    """
    owner_service: OwnerReviewService = synthetic_service(owner_id)  # [Owner group]: Same synthetic owner case as the Owner transport entry.
    wiring = OwnerReviewWiring(lambda: owner_service, owner_id, settings.guild_id, _utc_now)
    return JukeBoxClient(build_exchange_service(injection), interaction_guard=GuildInteractionGuard(settings), owner_review=wiring)


async def run_exchange_listen(arguments: argparse.Namespace, settings=None, owner_id=None) -> dict:
    """@description: Connect the labelled exchange client only after the reviewed gateway checks, and always close the client.
    @param arguments: Parsed listen arguments.
    @param settings: Explicit targets, loaded from the process environment when not supplied.
    @param owner_id: Configured Owner identity, loaded from the process environment when not supplied.
    @return: Public summary without credentials or identities.
    """
    validate_arguments(arguments)
    settings = settings or load_development_guild_settings()
    owner_id = owner_id or environment_snowflake("DISCORD_OWNER_USER_ID")
    async with build_exchange_client(settings, owner_id, arguments.inject) as client:
        summary = await listen_owner_transport(client, settings, authorized=arguments.authorize_gateway)
    summary["exchange_mode"] = arguments.inject
    summary["notice"] = SYNTHETIC_NOTICE
    return summary


def argument_parser() -> argparse.ArgumentParser:
    """@description: Keep the entry explicit: offline scenarios by default; the Discord listener needs its own gateway switch.
    @param: none
    @return: Argument parser.
    """
    parser = argparse.ArgumentParser(description="Labelled synthetic /exchange acceptance (scripted DM only, no real points, no MySQL)")
    parser.add_argument("mode", choices=("scenario", "listen"), nargs="?", default="scenario")
    parser.add_argument("--inject", choices=INJECTION_MODES, default="success")
    parser.add_argument("--authorize-gateway", action="store_true")
    parser.add_argument("--authorize-live-dm", action="store_true")
    return parser


def validate_arguments(arguments: argparse.Namespace) -> None:
    """@description: Refuse network modes without their switch, refuse live DM outright in this round, and keep scenario offline.
    @param arguments: Parsed arguments.
    @return: None; unauthorized combinations raise before any client is built.
    """
    if arguments.authorize_live_dm:
        raise RuntimeError("live DM sending is not part of this round; the entry only uses scripted outcomes")
    if arguments.mode == "scenario" and arguments.authorize_gateway:
        raise RuntimeError("scenario mode never connects")
    if arguments.mode == "listen" and not arguments.authorize_gateway:
        raise RuntimeError("listen requires --authorize-gateway")


class _Silent:
    """Ephemeral sink for offline scenario runs; nothing leaves the process."""

    async def send_ephemeral(self, text: str) -> None:
        """@description: Discard the reply in offline runs.
        @param self: Sink.
        @param text: Reply body.
        @return: None.
        """
        return None


def run_offline_scenarios(injection: str = "success") -> list[dict]:
    """@description: Run the reviewed requesters through the same service path with no network and no Discord.
    @param injection: Scripted outcome for this run.
    @return: One labelled summary per scenario.
    """
    service = build_exchange_service(injection)
    results = []
    for label, user_id, key in (("requester_with_point", 101, "scenario-1"), ("zero_point_user", 103, "scenario-2")):
        flow = asyncio.run(service.synthetic_exchange(user_id, key, None, _Silent()))
        results.append({"scenario": label, "reply": flow.reply_text, "delivery": getattr(flow.delivery_status, "value", None),
                        "refund": flow.refund_status, "labelled": SYNTHETIC_LABEL in flow.reply_text})
    return results


def main(argv: list[str] | None = None) -> int:
    """@description: Run the offline scenarios, or the guarded Discord listener through the shared acceptance boundary.
    @param argv: Optional argument list for tests.
    @return: Process exit code.
    """
    arguments = argument_parser().parse_args(argv)
    validate_arguments(arguments)
    if arguments.mode == "listen":
        execute_acceptance(arguments, run_exchange_listen)
        return 0
    print(SYNTHETIC_NOTICE)
    for item in run_offline_scenarios(arguments.inject):
        print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
