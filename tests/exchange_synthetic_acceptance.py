"""Run the labelled synthetic /exchange acceptance flow through the real service, with optional injected outcomes.

Fixture-only entry under tests/: the synthetic world lives in this process's fake database, and every DM or reply is labelled.
Injected outcomes exist only in this entry and are never reachable from normal users. Live DM sending needs its own switch.
The listen mode is intentionally not wired in this offline round.
"""

import argparse
import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

from bot.database.submission_repository import SubmissionRepository
from bot.exchange_delivery import DmResult
from bot.gateway_service import GatewayAcceptanceService
from bot.owner_review import OwnerReviewService
from bot.services import SYNTHETIC_LABEL, MusicService
from tests.intake_fixture import seed_intake
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

INJECTION_MODES = ("success", "not_sent", "uncertain", "refund_fail")  # [offline scripted outcomes]: Never sent to Discord.
LIVE_DM_SWITCH = "--authorize-live-dm"  # [explicit live switch]: Required before any real DM may be attempted.
FIXED_TEST_LINK = "https://www.youtube.com/watch?v={video_id}"  # [reviewed fixed pattern]: Placeholder, not a verified playable video.
SYNTHETIC_NOTICE = "合成資料：案件只存在於此程序記憶體，不寫入 MySQL，不代表真實投稿，不動用真實點數；連結為固定測試格式，不代表影片可播放。"
ORIGINAL_A = "synthetic-version-a"
ORIGINAL_B = "synthetic-version-b"


class InjectedDm:
    """Scripted direct-message outcome for this acceptance entry only; it never reaches Discord."""

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


class SyntheticExchangeService:
    """Route /exchange to the labelled synthetic flow and every other operation to the transport-only service."""

    def __init__(self, exchange: MusicService, database: FakeDatabase, injection: str, live_dm) -> None:
        """@description: Bind the synthetic exchange service, the fake database for fault arming, and the optional live adapter.
        @param self: Composite service.
        @param exchange: Synthetic-enabled MusicService over the fake repository.
        @param database: Fake database whose next refund statement may be made to fail once.
        @param injection: Scripted mode, or live for the real adapter.
        @param live_dm: Real adapter; used only when injection is live.
        @return: None.
        """
        self._exchange = exchange
        self._database = database
        self._injection = injection
        self._live = live_dm
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
        """@description: Run one labelled exchange; scripted modes replace the adapter, and live sending uses the live adapter only.
        @param self: Composite service.
        @param discord_user_id: Requester.
        @param request_key: Technical idempotency key.
        @param sender: Adapter supplied by the handler; ignored for scripted modes.
        @param replier: Ephemeral reply adapter.
        @return: Flow result.
        """
        if self._injection == "live":
            chosen = self._live
        else:
            chosen = InjectedDm(self._injection)
            if self._injection == "refund_fail":
                self._database.sql_fault = "UPDATE recommendations SET released_at"  # [one refund failure]: Pending path only.
        return await self._exchange.synthetic_exchange(discord_user_id, request_key, chosen, replier)

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


def build_exchange_service(injection: str, live_dm=None) -> SyntheticExchangeService:
    """@description: Assemble the labelled synthetic exchange service over the synthetic world.
    @param injection: One of the scripted modes, or live.
    @param live_dm: Real DM adapter, only when live sending is authorized.
    @return: Composite service for the Discord client.
    """
    if injection not in INJECTION_MODES + ("live",):
        raise ValueError("unknown injection mode")
    database, repository = build_world()
    exchange = MusicService(MagicMock(), submission_repository=repository, synthetic_exchange=True)
    return SyntheticExchangeService(exchange, database, injection, live_dm)


class _Silent:
    """Ephemeral sink for offline runs; nothing leaves the process."""

    async def send_ephemeral(self, text: str) -> None:
        """@description: Discard the reply in offline runs.
        @param self: Sink.
        @param text: Reply body.
        @return: None.
        """
        return None


def run_offline_scenarios(injection: str = "success") -> list[dict]:
    """@description: Run the reviewed requesters through the same service path with no network and no Discord.
    @param injection: Scripted outcome for every DM in this run.
    @return: One labelled summary per scenario.
    """
    service = build_exchange_service(injection)
    results = []
    for label, user_id, key in (("requester_with_point", 101, "scenario-1"), ("zero_point_user", 103, "scenario-2")):
        flow = asyncio.run(service.synthetic_exchange(user_id, key, None, _Silent()))
        results.append({"scenario": label, "reply": flow.reply_text, "delivery": getattr(flow.delivery_status, "value", None),
                        "refund": flow.refund_status, "labelled": flow.reply_text.startswith(SYNTHETIC_LABEL)})
    return results


def argument_parser() -> argparse.ArgumentParser:
    """@description: Keep the entry explicit: offline scenarios by default, and the listen path refused until it is wired.
    @param: none
    @return: Argument parser.
    """
    parser = argparse.ArgumentParser(description="Labelled synthetic /exchange acceptance (no real points, no MySQL)")
    parser.add_argument("mode", choices=("scenario", "listen"), nargs="?", default="scenario")
    parser.add_argument("--inject", choices=INJECTION_MODES, default="success")
    parser.add_argument("--authorize-gateway", action="store_true")
    parser.add_argument("--authorize-live-dm", action="store_true")
    return parser


def validate_arguments(arguments: argparse.Namespace) -> None:
    """@description: Refuse any network mode without its own switch, and any live success path without the live switch.
    @param arguments: Parsed arguments.
    @return: None; unauthorized combinations raise.
    """
    if arguments.mode == "scenario" and (arguments.authorize_gateway or arguments.authorize_live_dm):
        raise RuntimeError("scenario mode never connects and accepts no network switch")
    if arguments.mode == "listen" and not arguments.authorize_gateway:
        raise RuntimeError("listen requires --authorize-gateway")
    if arguments.mode == "listen" and not arguments.authorize_live_dm and arguments.inject == "success":
        raise RuntimeError("a live DM needs --authorize-live-dm; use --inject for scripted outcomes")


def main(argv: list[str] | None = None) -> int:
    """@description: Run the offline scenarios, or refuse the listen mode until its wiring is reviewed.
    @param argv: Optional argument list for tests.
    @return: Process exit code.
    """
    arguments = argument_parser().parse_args(argv)
    validate_arguments(arguments)
    if arguments.mode == "listen":
        raise RuntimeError("listen wiring is not part of this offline round")
    print(SYNTHETIC_NOTICE)
    for item in run_offline_scenarios(arguments.inject):
        print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
