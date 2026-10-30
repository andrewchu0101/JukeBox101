"""Run the Owner review commands through the real guarded client, backed by a labeled synthetic in-memory service.

Transport acceptance only: the synthetic submissions live in this process's fake database and never reach MySQL. Remote
synchronization publishes the Owner group alone, to the one authorized guild, and the listener checks that group's contract.
Fixture code under tests/ is used on purpose so production modules never import it.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import discord

from bot.acceptance_config import DevelopmentGuildSettings, environment_snowflake, load_development_guild_settings
from bot.acceptance_runtime import execute_acceptance
from bot.command_contract import normalize_command_payloads
from bot.discord_app import JukeBoxClient, OwnerReviewWiring
from bot.eligibility import DurationEvidence
from bot.database.submission_repository import SubmissionRepository
from bot.gateway_listener import GuildInteractionGuard
from bot.gateway_service import GatewayAcceptanceService
from bot.guild_acceptance import (
    AcceptanceError, REST_TIMEOUT_SECONDS, confirmation_text, local_preflight, login_smoke, require_empty_remote_guild,
    verify_remote_identity,
)
from bot.owner_review import OwnerReviewService
from bot.review import ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import VersionAssignment
from tests.intake_fixture import seed_intake
from tests.owner_transport_contract import (
    APPROVED_REMOTE_CHANGES, GLOBAL_COMMAND_NAMES, OWNER_GROUP_NAME as OWNER_NAME, diff_contracts, expected_five_contract,
    expected_six_contract, full_contract, remote_guild_contract, remote_payload, reviewed_remote_view,
)
from tests.submission_persistence_fake import FakeDatabase

OWNER_GROUP_NAME = "owner"
SWITCH_NAMES = ("authorize_login", "authorize_sync", "authorize_gateway", "authorize_guild_update")
# [mode switches]: Each mode accepts exactly these authorizations; sync needs login plus its own sync switch.
MODE_SWITCHES = {
    "preflight": frozenset(),
    "sync": frozenset({"authorize_login", "authorize_sync"}),
    "listen": frozenset({"authorize_gateway"}),
    "inspect": frozenset({"authorize_login"}),
    "update-guild": frozenset({"authorize_login", "authorize_guild_update"}),
}
SYNTHETIC_NOTICE = "合成資料：本入口的案件只存在於此程序記憶體中，不寫入 MySQL，也不代表真實投稿。"
SYNTHETIC_OPERATION_ID = "op-1"
TOKEN_LIFETIME = timedelta(minutes=5)


def _utc_now() -> datetime:
    """@description: Supply timezone-aware instants for token expiry and evidence timestamps.
    @param: none
    @return: Current UTC instant.
    """
    return datetime.now(timezone.utc)


def synthetic_submission() -> SubmissionRequest:
    """@description: Create one synthetic accepted submission whose duration passes and whose identity is unresolved.
    @param: none
    @return: Intake request for the synthetic operation.
    """
    video = VideoIdentity("Synth0001X1")  # [synthetic id]: Eleven characters, shaped like the YouTube identifiers the parser accepts.
    return SubmissionRequest(f"https://youtu.be/{video.video_id}", SYNTHETIC_OPERATION_ID, 101, 999,
                             VersionAssignment(video), ReviewSnapshot(video), 0,
                             DurationEvidence(video, 180, "synthetic-duration", True, True))


def synthetic_service(owner_id: int) -> OwnerReviewService:
    """@description: Build the Owner review service over one fresh fake database that holds one synthetic pending case.
    @param owner_id: Configured Owner Discord identity.
    @return: Owner review service whose writes stay in this process.
    """
    database = FakeDatabase()
    database.now = _utc_now().replace(tzinfo=None)  # [fake clock]: The fake stores naive database time.
    database.state["control"]["settlement_enabled"] = False  # [no payout]: Mirrors the disabled settlement state.
    repository = SubmissionRepository(database.connect, owner_id)
    seed_intake(repository, synthetic_submission())
    return OwnerReviewService(repository, owner_id, _utc_now, token_ttl=TOKEN_LIFETIME)


def build_synthetic_owner_client(settings: DevelopmentGuildSettings, owner_id: int) -> JukeBoxClient:
    """@description: Assemble the real guarded client with the Owner group wired to one synthetic service instance.
    @param settings: Explicit application and private guild targets.
    @param owner_id: Configured Owner Discord identity.
    @return: Client whose command tree admits only the configured guild and application.
    """
    service = synthetic_service(owner_id)  # [one instance]: Previews must survive between button presses.
    wiring = OwnerReviewWiring(lambda: service, owner_id, settings.guild_id, _utc_now)
    return JukeBoxClient(GatewayAcceptanceService(), interaction_guard=GuildInteractionGuard(settings), owner_review=wiring)


def owner_group_contract(client: JukeBoxClient, guild_id: int) -> list[dict]:
    """@description: Describe the Owner group as the guild would publish it, for comparison with the remote payload.
    @param client: Client whose local command tree holds the Owner group.
    @param guild_id: Private guild the group is registered for.
    @return: Normalized parameter contract of the guild's commands.
    """
    guild = discord.Object(id=guild_id)
    payloads = [command.to_dict(client.tree) for command in client.tree.get_commands(guild=guild)]
    return normalize_command_payloads(payloads)


async def sync_owner_group(client: JukeBoxClient, guild_id: int) -> list:
    """@description: Publish the guild's own commands only, so the global five are never copied into the private guild.
    @param client: Logged-in client whose local tree holds the Owner group.
    @param guild_id: Private guild to publish to.
    @return: Commands Discord reports as published.
    """
    return await asyncio.wait_for(client.tree.sync(guild=discord.Object(id=guild_id)), timeout=REST_TIMEOUT_SECONDS)


async def owner_guild_sync(client: JukeBoxClient, settings: DevelopmentGuildSettings, *, authorized: bool = False,
                           confirm=input) -> dict:
    """@description: Publish the Owner group to the private guild after identity, emptiness and typed confirmation checks.
    @param client: REST-authenticated transport client.
    @param settings: Expected application and private guild.
    @param authorized: Independent explicit opt-in for remote synchronization.
    @param confirm: Human input boundary; tests inject a deterministic answer.
    @return: Public summary of the published command names.
    """
    if authorized is not True:
        raise AcceptanceError("Owner sync requires separate explicit authorization")
    await verify_remote_identity(client, settings)
    await require_empty_remote_guild(client, settings.guild_id)
    prompt = (f"Owner transport sync\nApplication ID: {settings.application_id}\nGuild ID: {settings.guild_id}\n"
              f"Commands: {OWNER_GROUP_NAME}\nType {confirmation_text(settings)} to continue.")
    if confirm(prompt) != confirmation_text(settings):
        raise AcceptanceError("Owner sync cancelled; no remote commands changed")
    # [re-check after typing]: Another operator may have created a command while the confirmation was being typed.
    await verify_remote_identity(client, settings)
    await require_empty_remote_guild(client, settings.guild_id)
    published = await sync_owner_group(client, settings.guild_id)
    names = sorted(command.name for command in published)
    if names != [OWNER_GROUP_NAME]:
        raise AcceptanceError("Owner sync response differs from the Owner group; inspect remote state before any retry")
    return {"published_commands": names, "guild_id": settings.guild_id, "notice": SYNTHETIC_NOTICE}


async def inspect_remote_commands(client: JukeBoxClient, settings: DevelopmentGuildSettings) -> dict:
    """@description: Read-only listing of global and target-guild command names, so the operator sees the real remote state before any sync.
    @param client: REST-authenticated transport client.
    @param settings: Expected application and private guild.
    @return: Public summary with sorted command names per scope; never publishes, deletes or connects.
    """
    global_commands = await asyncio.wait_for(client.tree.fetch_commands(), timeout=REST_TIMEOUT_SECONDS)
    guild_commands = await asyncio.wait_for(client.tree.fetch_commands(guild=discord.Object(id=settings.guild_id)),
                                            timeout=REST_TIMEOUT_SECONDS)
    guild_contract = full_contract([remote_payload(command) for command in guild_commands])
    remote_five = [contract for contract in guild_contract if contract["name"] in GLOBAL_COMMAND_NAMES]
    differences = diff_contracts(expected_five_contract(client), remote_five)
    return {
        "application_id": settings.application_id,
        "guild_id": settings.guild_id,
        "global_commands": sorted(command.name for command in global_commands),
        "guild_commands": sorted(command.name for command in guild_commands),
        "guild_five_matches_local": not differences,
        "guild_five_differences": differences,
        "guild_owner_present": OWNER_NAME in {contract["name"] for contract in guild_contract},
        "guild_unreviewed_differences": _unreviewed_guild(client, settings.guild_id, guild_contract),
        "approved_remote_changes": [f"{change.command}.{change.field}: {change.remote_value!r} -> {change.local_value!r}"
                                    for change in APPROVED_REMOTE_CHANGES],
        "read_only": True,
        "notice": "唯讀盤點：未同步、未刪除、未連線 Gateway。",
    }


def _present_expected(client, guild_id: int, names: set[str]) -> list[dict]:
    """@description: Return the reviewed contract restricted to the commands the guild currently holds, so a missing Owner group is judged as an unpublished state.
    @param client: Synthetic transport client.
    @param guild_id: Private guild.
    @param names: Command names the guild currently holds.
    @return: Reviewed six when the Owner group exists, otherwise the reviewed five.
    """
    reviewed = expected_six_contract(client, guild_id)
    return reviewed if OWNER_NAME in names else [contract for contract in reviewed if contract["name"] != OWNER_NAME]


def _unreviewed_guild(client, guild_id: int, guild_contract: list[dict]) -> list[str]:
    """@description: List the differences that remain after the reviewed changes are applied, so only unreviewed drift is reported.
    @param client: Synthetic transport client.
    @param guild_id: Private guild.
    @param guild_contract: Remote contract of the guild's commands.
    @return: Unreviewed difference lines, or one explanation when a reviewed value no longer matches.
    """
    names = {contract["name"] for contract in guild_contract}
    try:
        return diff_contracts(_present_expected(client, guild_id, names), reviewed_remote_view(guild_contract))
    except AcceptanceError as error:
        return [str(error)]


def update_confirmation_text(settings: DevelopmentGuildSettings) -> str:
    """@description: Bind the guild update confirmation to one visible application and guild, distinct from the empty-guild sync text.
    @param settings: Explicit human-provided development targets.
    @return: Exact typed confirmation required before the update.
    """
    return f"UPDATE {settings.application_id} {settings.guild_id}"


def _update_prompt(settings: DevelopmentGuildSettings) -> str:
    """@description: Show the operator the exact reviewed set and what the update will and will not change.
    @param settings: Explicit development targets.
    @return: Prompt text shown before the typed confirmation.
    """
    names = ", ".join(sorted((*GLOBAL_COMMAND_NAMES, OWNER_NAME)))
    changes = "".join(f"Reviewed change: {change.command}.{change.field}: {change.remote_value!r} -> {change.local_value!r}\n"
                      for change in APPROVED_REMOTE_CHANGES)
    return (f"Guild update\nApplication ID: {settings.application_id}\nGuild ID: {settings.guild_id}\n"
            f"Reviewed commands to hold after the update: {names}\n{changes}"
            "Existing commands are republished with their reviewed full definitions; only the reviewed changes above differ; "
            f"{OWNER_NAME} is added or updated.\nType {update_confirmation_text(settings)} to continue.")


def _refuse_unreviewed_guild(client, guild_id: int, before: list[dict]) -> None:
    """@description: Stop the update unless the guild holds only reviewed commands and every difference is an approved change.
    @param client: Synthetic transport client.
    @param guild_id: Private guild.
    @param before: Remote guild contract read before the confirmation.
    @return: None; raises AcceptanceError with the first differences otherwise.
    """
    names = {contract["name"] for contract in before}
    extras = sorted(names - {*GLOBAL_COMMAND_NAMES, OWNER_NAME})
    if extras:
        raise AcceptanceError("Guild holds commands beyond the reviewed six: " + ", ".join(extras))
    differences = diff_contracts(_present_expected(client, guild_id, names), reviewed_remote_view(before))
    if differences:
        raise AcceptanceError("Guild commands differ from the reviewed contract beyond approved changes; stop without writing: "
                              + "; ".join(differences[:5]))


async def update_guild_sync(client: JukeBoxClient, settings: DevelopmentGuildSettings, *, authorized: bool = False,
                            confirm=input) -> dict:
    """@description: Add the Owner group to a guild that already holds the reviewed five, after full-contract, confirmation and race checks.
    @param client: REST-authenticated transport client.
    @param settings: Expected application and private guild.
    @param authorized: Separate explicit opt-in for updating an existing guild.
    @param confirm: Human input boundary; tests inject a deterministic answer.
    @return: Public summary of the outcome.
    """
    if authorized is not True:
        raise AcceptanceError("Guild update requires separate explicit authorization")
    await verify_remote_identity(client, settings)
    before = await remote_guild_contract(client, settings.guild_id)
    reviewed_six = expected_six_contract(client, settings.guild_id)
    if before == reviewed_six:
        return {"status": "already_matches", "guild_id": settings.guild_id, "notice": SYNTHETIC_NOTICE}
    _refuse_unreviewed_guild(client, settings.guild_id, before)
    if confirm(_update_prompt(settings)) != update_confirmation_text(settings):
        raise AcceptanceError("Guild update cancelled; no remote commands changed")
    # [race check]: Narrows the window only; another process may still change the guild between this read and the publish.
    await verify_remote_identity(client, settings)
    if await remote_guild_contract(client, settings.guild_id) != before:
        raise AcceptanceError("Guild commands changed during confirmation; stop without writing")
    client.tree.copy_global_to(guild=discord.Object(id=settings.guild_id))
    published = await asyncio.wait_for(client.tree.sync(guild=discord.Object(id=settings.guild_id)), timeout=REST_TIMEOUT_SECONDS)
    after = await remote_guild_contract(client, settings.guild_id)
    if after != reviewed_six:
        raise AcceptanceError("Remote result differs from the reviewed six or is unknown; stop and inspect the guild; do not retry")
    return {"status": "published_matches_expected", "published_commands": sorted(command.name for command in published),
            "guild_id": settings.guild_id, "notice": SYNTHETIC_NOTICE}


async def _refuse_remote_routing(client: JukeBoxClient, settings: DevelopmentGuildSettings) -> None:
    """@description: Refuse a listener whose application has an external interaction endpoint or a different identity.
    @param client: REST-authenticated transport client.
    @param settings: Expected application and private guild.
    @return: None; mismatches abort before connection.
    """
    application = await asyncio.wait_for(client.application_info(), timeout=REST_TIMEOUT_SECONDS)
    if application.id != settings.application_id or getattr(application, "interactions_endpoint_url", None):
        raise AcceptanceError("Application identity or external Interaction endpoint prevents verified gateway routing")


async def listen_owner_transport(client: JukeBoxClient, settings: DevelopmentGuildSettings, *, authorized: bool = False) -> dict:
    """@description: Connect the transport client only after explicit authorization and a read-only Owner-group contract match.
    @param client: Database-free transport client owned by the caller's async context.
    @param settings: Expected application and private guild.
    @param authorized: Explicit gateway authorization, including the necessary login and read-only REST checks.
    @return: Public summary; it does not claim manual Interaction acceptance passed.
    """
    if authorized is not True:
        raise AcceptanceError("Owner transport listen requires explicit gateway authorization")
    summary = local_preflight(client, settings)
    reviewed_six = expected_six_contract(client, settings.guild_id)
    await login_smoke(client, settings, authorized=True)
    await _refuse_remote_routing(client, settings)
    if await remote_guild_contract(client, settings.guild_id) != reviewed_six:
        raise AcceptanceError("Remote guild does not hold exactly the reviewed six commands; stop before connecting")
    summary["remote_contract"] = "reviewed_six_matched_before_connection"
    summary["notice"] = SYNTHETIC_NOTICE
    await client.connect(reconnect=False)
    summary["listener_stopped"] = True
    return summary


def argument_parser() -> argparse.ArgumentParser:
    """@description: Keep each network operation behind its own explicit switch, with a network-free preflight by default.
    @param: none
    @return: Parser for the transport acceptance modes.
    """
    parser = argparse.ArgumentParser(description="Owner transport acceptance with a labeled synthetic in-memory service")
    parser.add_argument("mode", choices=("preflight", "inspect", "sync", "update-guild", "listen"), nargs="?", default="preflight")
    parser.add_argument("--authorize-login", action="store_true")
    parser.add_argument("--authorize-sync", action="store_true")
    parser.add_argument("--authorize-guild-update", action="store_true")
    parser.add_argument("--authorize-gateway", action="store_true")
    return parser


def validate_transport_mode(arguments: argparse.Namespace) -> None:
    """@description: Reject unauthorized or mixed modes before reading secrets or creating a client.
    @param arguments: Parsed mode and switches.
    @return: None.
    """
    required_switches = MODE_SWITCHES.get(arguments.mode)
    if required_switches is None:
        raise AcceptanceError("Unsupported Owner transport mode")
    given_switches = frozenset(name for name in SWITCH_NAMES if getattr(arguments, name) is True)
    if given_switches != required_switches:
        raise AcceptanceError("Each mode accepts exactly its own authorization switches; a switch never stands in for another")


async def run_owner_transport(arguments: argparse.Namespace) -> dict:
    """@description: Own client cleanup for preflight, sync and listen, and keep the owner identity out of the summary.
    @param arguments: Parsed mode and explicit switches.
    @return: Public summary without credentials or database values.
    """
    validate_transport_mode(arguments)
    settings = load_development_guild_settings()
    owner_id = environment_snowflake("DISCORD_OWNER_USER_ID")
    async with build_synthetic_owner_client(settings, owner_id) as client:
        if arguments.mode == "preflight":
            summary = local_preflight(client, settings)
            summary["owner_group"] = [command["name"] for command in owner_group_contract(client, settings.guild_id)]
            summary["notice"] = SYNTHETIC_NOTICE
            return summary
        if arguments.mode == "inspect":
            await login_smoke(client, settings, authorized=arguments.authorize_login)
            return await inspect_remote_commands(client, settings)
        if arguments.mode == "sync":
            await login_smoke(client, settings, authorized=arguments.authorize_login)
            return await owner_guild_sync(client, settings, authorized=arguments.authorize_sync)
        if arguments.mode == "update-guild":
            await login_smoke(client, settings, authorized=arguments.authorize_login)
            return await update_guild_sync(client, settings, authorized=arguments.authorize_guild_update)
        return await listen_owner_transport(client, settings, authorized=arguments.authorize_gateway)


def main() -> None:
    """@description: Reuse the shared safe CLI boundary so credentials and raw tracebacks never reach the console.
    @param: none
    @return: None.
    """
    execute_acceptance(argument_parser().parse_args(), run_owner_transport)


if __name__ == "__main__":
    main()
