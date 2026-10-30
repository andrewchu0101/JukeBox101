"""Compare command parameter contracts without modifying any local or remote registry.

A versioned local baseline covers command types, ordered options, requiredness, choices, bounds, usable contexts,
install types and default member permissions.
"""

import json
from operator import itemgetter
from pathlib import Path

from bot.guild_acceptance import AcceptanceError

CONTRACT_PATH = Path(__file__).with_name("development_command_contract.json")
OPTION_DEFAULTS = {"required": False, "autocomplete": False, "min_value": None, "max_value": None,
                   "min_length": None, "max_length": None, "channel_types": []}


def normalize_command_payloads(payloads: list[dict]) -> list[dict]:
    """@description: Compare invocation contracts independently of generated IDs or descriptions.
    @param payloads: Local to_dict payloads or explicitly authorized remote read payloads.
    @return: Sorted command contracts with ordered parameter definitions preserved.
    """
    # [invocation contract]: Generated IDs and descriptions do not change accepted parameter shapes.
    contracts = [{"name": command["name"], "type": command.get("type", 1),
                  "contexts": sorted(command.get("contexts") or []),  # [where usable]: [0] means guild channels only.
                  "integration_types": sorted(command.get("integration_types") or []),  # [install type]: [0] guild install.
                  "default_member_permissions": command.get("default_member_permissions"),  # [None]: every member may use it.
                  "options": [_option_contract(option) for option in command.get("options", [])]}
                 for command in payloads]
    return sorted(contracts, key=itemgetter("name", "type"))


def _option_contract(option: dict) -> dict:
    """@description: Preserve option semantics that name-only comparisons would miss.
    @param option: Structured command parameter from local or remote registration.
    @return: Comparable parameter contract including nested structure, limits and choice values.
    """
    contract = {"name": option["name"], "type": option["type"]}  # [parameter semantics]: Preserve invocation constraints.
    contract.update({name: option.get(name, default) for name, default in OPTION_DEFAULTS.items()})
    contract["choices"] = [{"name": choice["name"], "value": choice["value"]} for choice in option.get("choices", [])]
    contract["options"] = [_option_contract(child) for child in option.get("options", [])]
    return contract


def verify_local_contract(tree) -> list[dict]:
    """@description: Stop preparation if the current registry drifts from the reviewed local parameter baseline.
    @param tree: Existing production-compatible local command registry.
    @return: Verified local invocation contract; not evidence about current Discord state.
    """
    baseline = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["commands"]  # [local baseline]: Never presented as a remote export.
    local_contract = normalize_command_payloads([command.to_dict(tree) for command in tree.get_commands()])
    if local_contract != normalize_command_payloads(baseline):
        raise AcceptanceError("Local command parameter contract differs from the reviewed baseline; do not sync")
    return local_contract


def verify_remote_payloads(local_contract: list[dict], payloads: list[dict]) -> None:
    """@description: Refuse gateway startup if a future authorized remote read shows parameter drift.
    @param local_contract: Reviewed local command parameter contract.
    @param payloads: Remote command to_dict payloads fetched without synchronization.
    @return: None; any structural mismatch stops before gateway connection.
    """
    if normalize_command_payloads(payloads) != local_contract:
        raise AcceptanceError("Remote command parameter contract differs; stop without synchronization")
