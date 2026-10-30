"""Describe and compare the full remote command contract: names, descriptions, permissions and every parameter.

Inspect, update and listen all judge the same reviewed set of six commands (five global commands plus the Owner group).
Fields Discord omits are filled with their defaults, so an omitted field and its default compare equal.
"""

import asyncio
import copy
from dataclasses import dataclass

import discord

from bot.guild_acceptance import AcceptanceError, REST_TIMEOUT_SECONDS

OWNER_GROUP_NAME = "owner"
GLOBAL_COMMAND_NAMES = ("balance", "daily", "exchange", "history", "share")
MAX_DIFFERENCES = 20  # [report size]: Enough to locate a drift without echoing a whole command set.


def _or(value, default):
    """@description: Fill an omitted or null remote field with its documented default.
    @param value: Field value as reported, or None when omitted.
    @param default: Documented default for the field.
    @return: The value, or the default when the value is None.
    """
    return default if value is None else value


def _choices(choices) -> list[dict]:
    """@description: Keep only the name and value of each choice, which are the parts that change what the user may send.
    @param choices: Choice payloads, or None.
    @return: Normalized choice list.
    """
    return [{"name": choice.get("name"), "value": choice.get("value")} for choice in choices or []]


def _option(option: dict) -> dict:
    """@description: Normalize one parameter, including its nested subcommand options, so every accepted shape is compared.
    @param option: Parameter payload as reported.
    @return: Normalized parameter contract.
    """
    return {
        "name": option.get("name"),
        "type": option.get("type"),
        "description": _or(option.get("description"), ""),
        "required": _or(option.get("required"), False),
        "choices": _choices(option.get("choices")),
        "options": [_option(child) for child in option.get("options") or []],
        "channel_types": list(option.get("channel_types") or []),
        "autocomplete": _or(option.get("autocomplete"), False),
        "min_value": option.get("min_value"),
        "max_value": option.get("max_value"),
        "min_length": option.get("min_length"),
        "max_length": option.get("max_length"),
    }


def _permissions(value):
    """@description: Compare default member permissions as integers whether the source is a number, a string or a Permissions object.
    @param value: Reported permission value, or None when unrestricted.
    @return: Integer permission bits, or None.
    """
    if value is None:
        return None
    return int(getattr(value, "value", value))


def _command(payload: dict) -> dict:
    """@description: Normalize one command payload into the full contract the reviewed set is judged by.
    @param payload: Command payload as reported locally or by Discord.
    @return: Normalized command contract.
    """
    return {
        "name": payload.get("name"),
        "type": _or(payload.get("type"), 1),
        "description": _or(payload.get("description"), ""),
        "default_member_permissions": _permissions(payload.get("default_member_permissions")),
        "dm_permission": _or(payload.get("dm_permission"), True),
        "nsfw": _or(payload.get("nsfw"), False),
        "contexts": payload.get("contexts"),
        "integration_types": payload.get("integration_types"),
        "options": [_option(option) for option in payload.get("options") or []],
    }


def full_contract(payloads: list[dict]) -> list[dict]:
    """@description: Normalize a command set into a deterministic full contract, ordered by name and type.
    @param payloads: Command payloads as reported locally or by Discord.
    @return: Sorted normalized contracts.
    """
    return sorted((_command(payload) for payload in payloads), key=lambda contract: (contract["name"], contract["type"]))


@dataclass(frozen=True)
class ReviewedChange:
    """Name one exact remote top-level field value that is expected to change, with its old and new value."""

    command: str  # [reviewed command]: Command name whose remote definition is expected to differ.
    field: str  # [reviewed field]: Top-level field name only; parameters are never exempted by this list.
    remote_value: object  # [old value]: The exact remote value observed before the update, None when unrestricted.
    local_value: object  # [new value]: The exact local value the update will publish.


# [reviewed drift]: The only remote differences accepted by update, shown to the operator as old-to-new before confirmation.
APPROVED_REMOTE_CHANGES = (
    ReviewedChange("history", "description", "查詢最近成功取得的推薦", "查詢本人投稿及目前審核狀態"),
    # [owner visibility]: The first update published the Owner group open to members; the reviewed value hides it by default.
    ReviewedChange(OWNER_GROUP_NAME, "default_member_permissions", None, 0),
)


def reviewed_remote_view(before: list[dict]) -> list[dict]:
    """@description: Apply only the reviewed changes to a remote contract; a field must hold the reviewed old or new value exactly, or the update stops.
    @param before: Remote normalized contract of the commands present in the guild.
    @return: Copy of the remote contract with each reviewed field set to its reviewed new value; absent commands are left to the diff.
    """
    view = copy.deepcopy(before)
    for change in APPROVED_REMOTE_CHANGES:
        contract = next((item for item in view if item["name"] == change.command), None)
        if contract is None:
            continue
        if contract[change.field] not in (change.remote_value, change.local_value):
            raise AcceptanceError(f"Reviewed change for {change.command}.{change.field} matches neither reviewed value; stop without writing")
        contract[change.field] = change.local_value
    return view


def _index(contracts: list[dict]) -> dict:
    """@description: Index normalized commands by name and type so differences are reported per command.
    @param contracts: Normalized command contracts.
    @return: Mapping from (name, type) to contract.
    """
    return {(contract["name"], contract["type"]): contract for contract in contracts}


def _walk(path: str, expected, actual, differences: list[str]) -> None:
    """@description: Record every field path whose remote value differs from the reviewed value, up to the report limit.
    @param path: Dotted location of the current value.
    @param expected: Reviewed value.
    @param actual: Remote value.
    @param differences: Accumulator for readable difference lines.
    @return: None.
    """
    if len(differences) >= MAX_DIFFERENCES:
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            _walk(f"{path}.{key}", expected.get(key), actual.get(key), differences)
    elif isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        for index, (reviewed, remote) in enumerate(zip(expected, actual)):
            _walk(f"{path}[{index}]", reviewed, remote, differences)
    elif expected != actual:
        differences.append(f"{path}: reviewed {expected!r}, remote {actual!r}")


def diff_contracts(expected: list[dict], actual: list[dict]) -> list[str]:
    """@description: List every difference between a reviewed contract and a remote contract, including missing and extra commands.
    @param expected: Reviewed normalized contract.
    @param actual: Remote normalized contract.
    @return: Readable difference lines; empty when the contracts are identical.
    """
    expected_index, actual_index = _index(expected), _index(actual)
    differences = [f"command missing remotely: {name}" for name, _type in sorted(set(expected_index) - set(actual_index))]
    differences += [f"command not reviewed: {name}" for name, _type in sorted(set(actual_index) - set(expected_index))]
    for key in sorted(set(expected_index) & set(actual_index)):
        _walk(key[0], expected_index[key], actual_index[key], differences)
    return differences[:MAX_DIFFERENCES]


def _local_payloads(client, guild=None) -> list[dict]:
    """@description: Serialize the local commands of one scope exactly as the tree would publish them.
    @param client: Client whose command tree holds the commands.
    @param guild: Guild object for guild scope, or None for the global scope.
    @return: Local command payloads.
    """
    return [command.to_dict(client.tree) for command in client.tree.get_commands(guild=guild)]


def local_global_payloads(client) -> list[dict]:
    """@description: Serialize the reviewed global commands, which are the five commands a guild copy publishes.
    @param client: Synthetic transport client.
    @return: Payloads of the global commands.
    """
    return _local_payloads(client)


def local_owner_payloads(client, guild_id: int) -> list[dict]:
    """@description: Serialize the Owner group registered for the private guild.
    @param client: Synthetic transport client.
    @param guild_id: Private guild the group is registered for.
    @return: Payloads of the Owner group.
    """
    return _local_payloads(client, discord.Object(id=guild_id))


def expected_five_contract(client) -> list[dict]:
    """@description: Return the full contract of the reviewed five global commands.
    @param client: Synthetic transport client.
    @return: Normalized contract of the five commands.
    """
    return full_contract(local_global_payloads(client))


def expected_six_contract(client, guild_id: int) -> list[dict]:
    """@description: Return the full contract of the reviewed six commands a private guild must hold after an update.
    @param client: Synthetic transport client.
    @param guild_id: Private guild.
    @return: Normalized contract of the six commands.
    """
    return full_contract(local_global_payloads(client) + local_owner_payloads(client, guild_id))


REMOTE_ATTRIBUTE_FIELDS = ("default_member_permissions", "dm_permission", "nsfw")


def remote_payload(command) -> dict:
    """@description: Rebuild a remote command payload from its object, adding the three top-level fields that to_dict leaves out.
    @param command: discord.py AppCommand or a fake with the same attributes.
    @return: Payload with default permissions, DM permission and nsfw taken from the object's attributes.
    """
    payload = command.to_dict()
    for field in REMOTE_ATTRIBUTE_FIELDS:
        payload[field] = getattr(command, field, None)
    return payload


async def remote_guild_contract(client, guild_id: int) -> list[dict]:
    """@description: Read the full contract of this application's commands in the guild, as a read-only REST call.
    @param client: REST-authenticated transport client.
    @param guild_id: Private guild.
    @return: Normalized remote contract.
    """
    remote = await asyncio.wait_for(client.tree.fetch_commands(guild=discord.Object(id=guild_id)), timeout=REST_TIMEOUT_SECONDS)
    return full_contract([remote_payload(command) for command in remote])
