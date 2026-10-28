"""Run the labelled synthetic /exchange acceptance through the real Discord client, with scripted DM outcomes only.

Fixture-only entry under tests/: the synthetic world lives in this process's fake database and every DM is scripted.
No real direct message is sent and no real points are touched. Scripted modes are chosen at startup and cannot be changed
by a Discord user. Live DM sending is not part of this round, so --authorize-live-dm is refused explicitly.

Participants: the Owner (DISCORD_OWNER_USER_ID) is funded with one synthetic point and holds recording version A.
An optional partner and an optional zero-point user come from JUKEBOX_SYNTHETIC_PARTNER_USER_ID and
JUKEBOX_SYNTHETIC_ZERO_USER_ID. Unset participants receive nothing and cannot use the entry.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass, replace
from datetime import timedelta
from unittest.mock import MagicMock

from bot.acceptance_config import DevelopmentGuildSettings, environment_snowflake, load_development_guild_settings
from bot.acceptance_runtime import execute_acceptance
from bot.database.submission_repository import SubmissionRepository
from bot.discord_app import JukeBoxClient, OwnerReviewWiring
from bot.exchange_delivery import DeliveryStatus, DmResult, ExchangeFlowResult
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
PARTNER_ENV = "JUKEBOX_SYNTHETIC_PARTNER_USER_ID"  # [optional participant]: Second Discord user, set only by the operator.
ZERO_ENV = "JUKEBOX_SYNTHETIC_ZERO_USER_ID"  # [optional participant]: Discord user with no points, set only by the operator.
INTERNAL_PARTNER_SEED = 1002  # [internal seed, not a Discord ID]: Holds version B when no partner is configured; no caller can reach it.
INTERNAL_ZERO_SEED = 1003  # [internal seed, not a Discord ID]: Accepted intake only; no caller can reach it.
NOT_SENT_NOTICE = "未實際發送 DM（合成注入結果）。"  # [visible boundary]: Every reply in this entry says no DM was sent.
UNCONFIGURED_TEXT = "此帳號未設定為合成測試者；未扣點、未建立紀錄。"
FIXED_TEST_LINK = "https://www.youtube.com/watch?v={video_id}"  # [reviewed fixed pattern]: Placeholder, not a verified playable video.
SYNTHETIC_NOTICE = "合成資料：案件只存在於此程序記憶體，不寫入 MySQL，不代表真實投稿，不動用真實點數，不實際發送 DM；連結為固定測試格式，不代表影片可播放。"
ORIGINAL_A = "synthetic-version-a"
ORIGINAL_B = "synthetic-version-b"
DELIVERY_LABELS = {"delivered": "已交付（合成）", "failed": "未送出（注入）", "uncertain": "結果未知（注入，保留）", "sending": "交付中",
                 "reserved": "已保留，待確認（尚未派送）"}  # [reserved]: Never claimed; shown as awaiting confirmation, not as delivered.
REFUND_LABELS = {"refunded": "已退還 1 Nickel", "pending": "退款待處理", "none": "無退款"}


@dataclass(frozen=True)
class SyntheticAccounts:
    """Name the synthetic participants and refuse ambiguous or duplicate identities."""

    owner: int  # [funded participant]: Holds version A and receives one point through a real settlement in the fake.
    partner: int | None  # [optional participant]: Holds version B when configured; otherwise an internal seed holds it.
    zero: int | None  # [optional participant]: Accepted intake only; holds no points.

    def __post_init__(self) -> None:
        """@description: Reject non-positive or duplicated identities before any seed is written.
        @param self: Account set.
        @return: None; invalid sets raise.
        """
        given = [value for value in (self.owner, self.partner, self.zero) if value is not None]
        if any(not isinstance(value, int) or value <= 0 for value in given):
            raise ValueError("synthetic account identifiers must be positive integers")
        if len(set(given)) != len(given):
            raise ValueError("synthetic account identifiers must be distinct")

    @property
    def callers(self) -> frozenset:
        """@description: List the Discord identities allowed to use this entry; internal seeds are never callers.
        @param self: Account set.
        @return: Configured caller identifiers.
        """
        return frozenset(value for value in (self.owner, self.partner, self.zero) if value is not None)


def optional_snowflake(name: str) -> int | None:
    """@description: Read an optional synthetic participant from the process environment with strict decimal validation.
    @param name: Environment key of the optional participant.
    @return: Positive identifier, or None when the setting is absent.
    """
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    if not value.isascii() or not value.isdecimal() or value.startswith("0"):
        raise ValueError(f"{name} must be a positive decimal identifier")
    return int(value)


def accounts_from_environment(owner_id: int) -> SyntheticAccounts:
    """@description: Build the participant set from the Owner identity and the two optional operator settings.
    @param owner_id: Configured Owner identity.
    @return: Validated account set.
    """
    return SyntheticAccounts(owner=owner_id, partner=optional_snowflake(PARTNER_ENV), zero=optional_snowflake(ZERO_ENV))


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


class _Silent:
    """Ephemeral sink for the inner flow; the entry sends its own labelled reply."""

    async def send_ephemeral(self, text: str) -> None:
        """@description: Discard the inner reply.
        @param self: Sink.
        @param text: Reply body.
        @return: None.
        """
        return None


def build_world(accounts: SyntheticAccounts):
    """@description: Create the synthetic fake database: the funded participant holds A, the other side holds B, and the zero-point seed has intake only.
    @param accounts: Participant set.
    @return: Tuple of fake database and synthetic repository.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)  # [fake clock]: Naive database time, as the fake stores it.
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: START, token_ttl=timedelta(minutes=5))
    seeds = ((1, accounts.owner, ORIGINAL_A), (2, accounts.partner or INTERNAL_PARTNER_SEED, ORIGINAL_B))
    for number, submitter, original in seeds:
        seed_intake(repository, replace(submission(number), submitter_id=submitter))
        confirm_version(service, number, owner_evidence(video_of(number), f"ev-{number}", original))
        approve(service, number)
        repository.settle(f"op-{number}")
    zero = accounts.zero or INTERNAL_ZERO_SEED
    seed_intake(repository, replace(submission(3), submitter_id=zero))  # [zero-point seed]: Accepted, never settled.
    verify_all_songs(database)
    for row in database.state["songs"].values():
        row.update(youtube_url=FIXED_TEST_LINK.format(video_id=row["platform_track_id"]), verification_provenance="synthetic-fixed-test-link")
    return database, repository


class SyntheticExchangeService:
    """Route /exchange, /balance and /history to the labelled synthetic flow; every other operation goes to the transport-only service."""

    def __init__(self, exchange: MusicService, database: FakeDatabase, repository: SubmissionRepository, injection: str,
                 accounts: SyntheticAccounts) -> None:
        """@description: Bind the synthetic services, the fake database for fault arming, the startup-selected outcome and the participant set.
        @param self: Composite service.
        @param exchange: Synthetic-enabled MusicService over the fake repository.
        @param database: Fake database whose next refund statement may be made to fail once.
        @param repository: Synthetic repository used for the balance and history projections.
        @param injection: Scripted outcome selected at startup.
        @param accounts: Participant set; only these identities may call the entry.
        @return: None.
        """
        if injection not in INJECTION_MODES:
            raise ValueError("unknown injection mode")
        self._exchange = exchange
        self._database = database
        self._repository = repository
        self._injection = injection
        self._accounts = accounts
        self._transport = GatewayAcceptanceService()
        self._refund_fault_armed = injection == "refund_fail"  # [one refund failure]: Armed once per process so a later retry can succeed.

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
        return "交換功能待歌曲資格驗證與派送恢復流程完成後開放；本次未扣除 Nickel。"

    async def synthetic_exchange(self, discord_user_id: int, request_key: str, sender, replier) -> ExchangeFlowResult:
        """@description: Run one labelled exchange with the scripted adapter; the handler's real adapter is never used.
        @param self: Composite service.
        @param discord_user_id: Requester.
        @param request_key: Technical idempotency key for this interaction.
        @param sender: Real adapter from the handler, deliberately unused here.
        @param replier: Ephemeral reply adapter.
        @return: Flow result whose reply text is labelled and states that no DM was sent.
        """
        del sender  # [no real DM]: The handler's adapter is never called in this entry.
        if discord_user_id not in self._accounts.callers:
            return await self._say(replier, ExchangeFlowResult(UNCONFIGURED_TEXT, False, None, None, None))
        if self._refund_fault_armed:
            self._database.sql_fault = "UPDATE recommendations SET released_at"  # [one refund failure]: Pending path only.
            self._refund_fault_armed = False
        flow = await self._exchange.synthetic_exchange(discord_user_id, request_key, InjectedDm(self._injection), _Silent())
        return await self._say(replier, replace(flow, reply_text=_exchange_text(flow)))

    async def _say(self, replier, flow: ExchangeFlowResult) -> ExchangeFlowResult:
        """@description: Send the entry's own labelled reply; a lost reply is reported and never changes the recorded result.
        @param self: Composite service.
        @param replier: Ephemeral reply adapter.
        @param flow: Flow result to show.
        @return: The same result with the reply transport status.
        """
        try:
            await replier.send_ephemeral(flow.reply_text)
        except Exception:
            return replace(flow, reply_failed=True)
        return flow

    async def balance(self, discord_user_id: int) -> str:
        """@description: Show the caller's own synthetic balance, from the same in-memory repository the exchange uses.
        @param self: Composite service.
        @param discord_user_id: Requester.
        @return: Labelled balance text.
        """
        if discord_user_id not in self._accounts.callers:
            return UNCONFIGURED_TEXT
        credits = await asyncio.to_thread(self._repository.user_exchange_balance, discord_user_id)
        if credits is None:
            return UNCONFIGURED_TEXT
        return f"{SYNTHETIC_LABEL}假餘額：{credits} 點（合成資料，未動真實點數）。"

    async def history(self, discord_user_id: int) -> str:
        """@description: Show the caller's own synthetic exchange results with operation codes and states, never a link.
        @param self: Composite service.
        @param discord_user_id: Requester.
        @return: Labelled history text.
        """
        if discord_user_id not in self._accounts.callers:
            return UNCONFIGURED_TEXT
        rows = await asyncio.to_thread(self._repository.user_exchange_results, discord_user_id)
        if not rows:
            return f"{SYNTHETIC_LABEL}尚無合成交換紀錄。"
        lines = [f"操作 {row['operation_code']}｜合成歌曲 #{row['song_id']}｜{DELIVERY_LABELS[row['delivery_state']]}｜"
                 f"{REFUND_LABELS[row['refund_state']]}" for row in rows]
        return SYNTHETIC_LABEL + "\n".join(lines)

    def __getattr__(self, name: str):
        """@description: Delegate every other handler-facing operation to the transport-only service.
        @param self: Composite service.
        @param name: Operation name.
        @return: The transport operation.
        """
        return getattr(self._transport, name)


def _exchange_text(flow: ExchangeFlowResult) -> str:
    """@description: Word the scripted result so simulated outcomes never read as real delivery.
    @param flow: Flow result from the labelled service path.
    @return: Labelled reply text stating that no DM was sent.
    """
    if flow.recommendation_id is None:
        return flow.reply_text  # [no reservation]: Already labelled, and it states the refusal reason.
    code = f"操作 {flow.recommendation_id}"
    if flow.delivery_status is DeliveryStatus.DELIVERED:
        return f"{SYNTHETIC_LABEL}合成交換成功；未實際發送 DM。{code}"
    if flow.delivery_status is DeliveryStatus.FAILED:
        if flow.refund_status in ("refunded", "already_released"):
            return f"{SYNTHETIC_LABEL}模擬未送出（注入），已退還 1 Nickel；未實際發送 DM。{code}"
        return f"{SYNTHETIC_LABEL}模擬未送出（注入），退款待處理；未實際發送 DM。{code}"
    if flow.delivery_status is DeliveryStatus.UNCERTAIN:
        return f"{SYNTHETIC_LABEL}模擬結果未知（注入）：保留紀錄，不退款、不重送；未實際發送 DM。{code}"
    return f"{SYNTHETIC_LABEL}此交換仍在交付中，請稍後查詢，不會重複送出。{code}"


def build_exchange_service(injection: str, accounts: SyntheticAccounts) -> SyntheticExchangeService:
    """@description: Assemble the labelled scripted exchange service over the synthetic world.
    @param injection: Scripted outcome selected at startup.
    @param accounts: Participant set.
    @return: Composite service for the Discord client.
    """
    database, repository = build_world(accounts)
    exchange = MusicService(MagicMock(), submission_repository=repository, synthetic_exchange=True)
    return SyntheticExchangeService(exchange, database, repository, injection, accounts)


def build_exchange_client(settings: DevelopmentGuildSettings, owner_id: int, injection: str,
                          accounts: SyntheticAccounts) -> JukeBoxClient:
    """@description: Assemble the real guarded client with the labelled exchange service and the Owner group, as the Owner transport does.
    @param settings: Explicit application and private guild targets.
    @param owner_id: Configured Owner Discord identity.
    @param injection: Scripted outcome selected at startup.
    @param accounts: Participant set.
    @return: Client whose command tree admits only the configured guild and application.
    """
    owner_service: OwnerReviewService = synthetic_service(owner_id)  # [Owner group]: Same synthetic owner case as the Owner transport entry.
    wiring = OwnerReviewWiring(lambda: owner_service, owner_id, settings.guild_id, _utc_now)
    return JukeBoxClient(build_exchange_service(injection, accounts), interaction_guard=GuildInteractionGuard(settings),
                         owner_review=wiring)


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
    accounts = accounts_from_environment(owner_id)
    async with build_exchange_client(settings, owner_id, arguments.inject, accounts) as client:
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


def run_offline_scenarios(injection: str = "success") -> list[dict]:
    """@description: Run the reviewed participants through the same service path with no network and no Discord.
    @param injection: Scripted outcome for this run.
    @return: One labelled summary per scenario.
    """
    accounts = SyntheticAccounts(owner=1001, partner=None, zero=1003)  # [offline identities]: Internal placeholders, never Discord IDs.
    service = build_exchange_service(injection, accounts)
    results = []
    for label, user_id, key in (("owner_with_point", accounts.owner, "scenario-1"), ("owner_second_request", accounts.owner, "scenario-2"),
                                ("zero_point_user", accounts.zero, "scenario-3")):
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
