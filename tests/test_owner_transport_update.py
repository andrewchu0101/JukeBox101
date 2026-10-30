"""Verify the full remote contract, the reviewed six-command set and the guarded update of an existing guild.

Fake REST objects and a synthetic client only; no login, Gateway, remote synchronization or database connection is made.
"""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.acceptance_config import load_development_guild_settings
from bot.guild_acceptance import AcceptanceError
from tests.owner_transport_acceptance import (
    argument_parser,
    build_synthetic_owner_client,
    inspect_remote_commands,
    listen_owner_transport,
    update_guild_sync,
    validate_transport_mode,
)
from tests.owner_transport_contract import (
    diff_contracts,
    expected_six_contract,
    full_contract,
    local_owner_payloads,
)
from tests.test_discord_owner_review import OWNER_ID, _run

GUILD_ID = 2002
APPLICATION_ID = 5005


@pytest.fixture
def settings(monkeypatch):
    """@description: Provide synthetic, secret-safe development targets without any database variable.
    @param monkeypatch: Environment isolation facility.
    @return: Explicit synthetic development guild settings.
    """
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-token-secret")
    monkeypatch.setenv("DISCORD_DEV_GUILD_ID", str(GUILD_ID))
    monkeypatch.setenv("DISCORD_EXPECTED_APPLICATION_ID", str(APPLICATION_ID))
    return load_development_guild_settings()


@pytest.fixture
def client():
    """@description: Build the synthetic transport client with the Owner group wired to the private guild.
    @return: Transport acceptance client.
    """
    return build_synthetic_owner_client(_settings_stub(), OWNER_ID)


def _settings_stub():
    """@description: Provide the guild targets the synthetic client needs without reading the environment.
    @return: Development settings object with synthetic identifiers.
    """
    from bot.acceptance_config import DevelopmentGuildSettings
    from bot.config import DiscordSettings
    return DevelopmentGuildSettings(DiscordSettings(token="synthetic-token-secret"), GUILD_ID, APPLICATION_ID)


REMOTE_ONLY_FIELDS = ("default_member_permissions", "dm_permission", "nsfw")


def _remote_object(payload: dict) -> SimpleNamespace:
    """@description: Build one fake remote command the way discord.py's AppCommand holds it: three top-level fields are attributes, absent from to_dict.
    @param payload: Command payload the fake represents.
    @return: Fake remote command object.
    """
    permissions = payload.get("default_member_permissions")
    attributes = {
        "name": payload["name"],
        "default_member_permissions": None if permissions is None else SimpleNamespace(value=int(permissions)),
        "dm_permission": payload.get("dm_permission", True),
        "nsfw": payload.get("nsfw", False),
        "to_dict": lambda payload=payload: {key: value for key, value in copy.deepcopy(payload).items() if key not in REMOTE_ONLY_FIELDS},
    }
    return SimpleNamespace(**attributes)


def remote_objects(payloads: list[dict]) -> list:
    """@description: Wrap payload dictionaries as fake remote command objects with discord.py's attribute and to_dict shape.
    @param payloads: Remote command payloads.
    @return: Fake remote command objects.
    """
    return [_remote_object(payload) for payload in payloads]


def local_five_payloads(client) -> list[dict]:
    """@description: Serialize the five global commands exactly as the remote guild would hold them after a copy.
    @param client: Synthetic transport client.
    @return: Payloads of the five global commands.
    """
    return [command.to_dict(client.tree) for command in client.tree.get_commands()]


def stub_remote(client, monkeypatch, *, guild_commands: list, global_commands: list | None = None):
    """@description: Replace login, identity, command reads and publish calls with fakes that observe every remote write.
    @param client: Synthetic transport client.
    @param monkeypatch: Attribute replacement facility.
    @param guild_commands: Mutable list of the guild's remote command payloads, updated by fake publishes.
    @param global_commands: Mutable list of global remote command payloads.
    @return: Mock recording the publish call.
    """
    globals_ = [] if global_commands is None else global_commands

    def fetch(guild=None):
        """@description: Return the remote global or guild command objects.
        @param guild: Guild object for guild reads, or None for global reads.
        @return: Awaitable of fake command objects.
        """
        source = globals_ if guild is None else guild_commands
        return _awaitable(remote_objects(source))

    monkeypatch.setattr(type(client), "application_id", property(lambda _self: APPLICATION_ID))
    monkeypatch.setattr(client, "login", AsyncMock())
    monkeypatch.setattr(client, "application_info", AsyncMock(return_value=SimpleNamespace(id=APPLICATION_ID, interactions_endpoint_url=None)))
    monkeypatch.setattr(client, "fetch_guild", AsyncMock(return_value=SimpleNamespace(id=GUILD_ID)))
    monkeypatch.setattr(client, "connect", AsyncMock())
    monkeypatch.setattr(client.tree, "fetch_commands", fetch)
    copy_spy = MagicMock(wraps=client.tree.copy_global_to)
    monkeypatch.setattr(client.tree, "copy_global_to", copy_spy)
    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(client.tree, "sync", sync)
    return sync


async def _awaitable(value):
    """@description: Wrap one value as a coroutine so fake REST reads match the real async interface.
    @param value: Value to return.
    @return: The value.
    """
    return value


def owner_sync_publisher(guild_commands: list, expected_six: list):
    """@description: Build a fake publish that replaces the guild's remote set with the given six payloads, as Discord does.
    @param guild_commands: Mutable remote guild list to replace.
    @param expected_six: Payloads the publish should install.
    @return: Async fake for tree.sync.
    """
    async def publish(guild=None):
        """@description: Replace the remote guild commands with the published set.
        @param guild: Guild the publish targets.
        @return: Fake published command objects.
        """
        guild_commands[:] = copy.deepcopy(expected_six)
        return remote_objects(expected_six)

    return publish


# ---- full contract and diff ----

def test_full_contract_detects_a_changed_choice_description_permission_or_extra_option(client):
    """@description: The full contract covers choices, descriptions, default permissions and parameters, not only names.
    @param client: Synthetic transport client.
    @return: None.
    """
    base = local_five_payloads(client)
    expected = full_contract(base)
    cases = {
        "description": lambda p: p.__setitem__("description", "changed text"),
        "permission": lambda p: p.__setitem__("default_member_permissions", "8"),
        "dm": lambda p: p.__setitem__("dm_permission", not p.get("dm_permission", True)),
        "extra option": lambda p: p.setdefault("options", []).append({"name": "extra", "type": 3, "description": "e"}),
    }
    for label, mutate in cases.items():
        changed = copy.deepcopy(base)
        mutate(changed[0])
        assert diff_contracts(expected, full_contract(changed)), label
    assert diff_contracts(expected, full_contract(base)) == []


def test_full_contract_treats_absent_defaults_and_remote_key_order_as_equal(client):
    """@description: Discord may omit a default field; the contract treats the omitted and default forms as the same command.
    @param client: Synthetic transport client.
    @return: None.
    """
    base = local_five_payloads(client)
    trimmed = copy.deepcopy(base)
    for payload in trimmed:
        payload.pop("nsfw", None)
    assert diff_contracts(full_contract(base), full_contract(trimmed)) == []
    # v7: contexts=[0] (guild only) is a deliberate safety constraint, not a default; a remote set without it differs.
    without_contexts = copy.deepcopy(base)
    for payload in without_contexts:
        payload.pop("contexts", None)
    assert diff_contracts(full_contract(base), full_contract(without_contexts))


# ---- read-only inspection reports the remote five against the local five ----

def test_inspect_reports_a_match_when_the_remote_five_equal_the_local_five(client, settings, monkeypatch):
    """@description: Inspection says the remote five match the local five and writes nothing.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = local_five_payloads(client)
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    summary = _run(inspect_remote_commands(client, settings))
    assert summary["guild_five_matches_local"] is True
    assert summary["guild_five_differences"] == []
    sync.assert_not_awaited()


def test_inspect_reports_the_difference_when_one_remote_choice_differs(client, settings, monkeypatch):
    """@description: A remote command whose parameter choice differs is reported with its path, not silently accepted.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = local_five_payloads(client)
    with_choice = next(payload for payload in guild if payload.get("options"))
    with_choice["options"][0]["description"] = "remote-only description"
    stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    summary = _run(inspect_remote_commands(client, settings))
    assert summary["guild_five_matches_local"] is False
    assert any("remote-only description" in line or "description" in line for line in summary["guild_five_differences"])


# ---- guarded update of an existing guild ----

def test_update_refuses_when_a_remote_five_command_differs_from_the_local_five(client, settings, monkeypatch):
    """@description: An existing guild whose five commands differ is never overwritten; the update stops before any confirmation.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = local_five_payloads(client)
    guild[0]["description"] = "remote drift"
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    confirm = MagicMock(return_value="UPDATE 5005 2002")
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=confirm))
    confirm.assert_not_called()
    sync.assert_not_awaited()


def test_update_refuses_an_extra_remote_command_beyond_the_reviewed_five(client, settings, monkeypatch):
    """@description: A guild holding any command beyond the reviewed five is refused, because the update would otherwise change it.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = local_five_payloads(client) + [{"name": "legacy-extra", "type": 1, "description": "x", "options": []}]
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    sync.assert_not_awaited()


def test_update_is_a_no_op_when_the_guild_already_holds_exactly_the_reviewed_six(client, settings, monkeypatch):
    """@description: When the remote guild already matches the reviewed six, the update reports that and publishes nothing.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    six = expected_six_contract(client, GUILD_ID)
    sync = stub_remote(client, monkeypatch, guild_commands=copy.deepcopy(six), global_commands=[])
    summary = _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    assert summary["status"] == "already_matches"
    sync.assert_not_awaited()


def test_update_refuses_a_wrong_typed_confirmation_before_any_write(client, settings, monkeypatch):
    """@description: A confirmation string that does not name this application and guild stops the update before publishing.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    sync = stub_remote(client, monkeypatch, guild_commands=local_five_payloads(client), global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 9999 2002"))
    sync.assert_not_awaited()


def test_update_refuses_when_the_remote_guild_changes_during_confirmation(client, settings, monkeypatch):
    """@description: A remote change made while the operator types the confirmation stops the update before publishing.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = local_five_payloads(client)
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])

    def change_then_confirm(prompt: str) -> str:
        """@description: Simulate another operator changing the guild while the confirmation is typed.
        @param prompt: Confirmation prompt, unused by the fake.
        @return: The correct typed confirmation.
        """
        guild.append({"name": "late-arrival", "type": 1, "description": "x", "options": []})
        return "UPDATE 5005 2002"

    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=change_then_confirm))
    sync.assert_not_awaited()


def test_update_publishes_the_reviewed_six_and_verifies_the_full_contract_afterwards(client, settings, monkeypatch):
    """@description: The normal path copies the five globals, publishes the six, and verifies the complete remote contract.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = local_five_payloads(client)
    six = expected_six_contract(client, GUILD_ID)
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    sync.side_effect = owner_sync_publisher(guild, six)
    summary = _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    assert summary["status"] == "published_matches_expected"
    assert sorted(summary["published_commands"]) == ["balance", "daily", "exchange", "history", "owner", "share"]
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["guild"].id == GUILD_ID


def test_update_stops_without_retry_when_the_remote_result_differs_from_the_expected_six(client, settings, monkeypatch):
    """@description: A publish whose remote result is not the reviewed six stops the update; it is never retried.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = local_five_payloads(client)
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])

    async def publish_nothing_useful(guild=None):
        """@description: Simulate a publish whose result leaves the guild different from the expected six.
        @param guild: Guild the publish targets.
        @return: Empty published list.
        """
        return []

    sync.side_effect = publish_nothing_useful
    with pytest.raises(AcceptanceError, match="do not retry"):
        _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    sync.assert_awaited_once()


def test_update_requires_its_own_authorization_and_refuses_without_it(client, settings, monkeypatch):
    """@description: The guild update refuses to read or publish without its separate authorization.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    sync = stub_remote(client, monkeypatch, guild_commands=local_five_payloads(client), global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=False, confirm=lambda prompt: "UPDATE 5005 2002"))
    sync.assert_not_awaited()


# ---- listen accepts the same reviewed six ----

def test_listen_accepts_the_reviewed_six_and_connects(client, settings, monkeypatch):
    """@description: Listen connects when the remote guild equals the reviewed six.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    six = expected_six_contract(client, GUILD_ID)
    stub_remote(client, monkeypatch, guild_commands=copy.deepcopy(six), global_commands=[])
    summary = _run(listen_owner_transport(client, settings, authorized=True))
    assert summary["listener_stopped"] is True
    client.connect.assert_awaited_once()


def test_listen_refuses_before_connecting_when_the_guild_holds_only_the_five(client, settings, monkeypatch):
    """@description: Listen refuses to connect while the guild still lacks the Owner group, so the bot never runs unpublished.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    stub_remote(client, monkeypatch, guild_commands=local_five_payloads(client), global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(listen_owner_transport(client, settings, authorized=True))
    client.connect.assert_not_awaited()


# ---- switches ----

def test_update_mode_accepts_only_its_own_switches():
    """@description: The update mode needs login plus its own guild-update switch, and sync never accepts that switch.
    @param: none
    @return: None.
    """
    validate_transport_mode(argument_parser().parse_args(["update-guild", "--authorize-login", "--authorize-guild-update"]))
    with pytest.raises(AcceptanceError):
        validate_transport_mode(argument_parser().parse_args(["update-guild", "--authorize-login"]))
    with pytest.raises(AcceptanceError):
        validate_transport_mode(argument_parser().parse_args(["sync", "--authorize-login", "--authorize-sync",
                                                              "--authorize-guild-update"]))


# ---- the one reviewed remote change: history.description ----

REMOTE_HISTORY_DESCRIPTION = "查詢最近成功取得的推薦"


def remote_with_reviewed_description(client) -> list[dict]:
    """@description: Build the remote five as currently observed, where only history.description carries the reviewed old value.
    @param client: Synthetic transport client.
    @return: Remote command payloads with the reviewed old description.
    """
    guild = local_five_payloads(client)
    next(payload for payload in guild if payload["name"] == "history")["description"] = REMOTE_HISTORY_DESCRIPTION
    return guild


def test_update_accepts_exactly_the_reviewed_history_description_change_and_shows_it_in_the_prompt(client, settings, monkeypatch):
    """@description: The only reviewed difference is accepted, shown as old-to-new text, and the publish result must match the full six.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = remote_with_reviewed_description(client)
    six = expected_six_contract(client, GUILD_ID)
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    sync.side_effect = owner_sync_publisher(guild, six)
    prompts = []

    def confirm(prompt: str) -> str:
        """@description: Record the shown prompt and type the correct confirmation.
        @param prompt: Prompt text shown to the operator.
        @return: The correct typed confirmation.
        """
        prompts.append(prompt)
        return "UPDATE 5005 2002"

    summary = _run(update_guild_sync(client, settings, authorized=True, confirm=confirm))
    assert summary["status"] == "published_matches_expected"
    assert REMOTE_HISTORY_DESCRIPTION in prompts[0] and "查詢本人投稿及目前審核狀態" in prompts[0]


def test_update_refuses_when_the_history_description_is_neither_the_reviewed_old_value_nor_the_new_one(client, settings, monkeypatch):
    """@description: A history description other than the reviewed old value is an unexpected drift and stops the update.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = remote_with_reviewed_description(client)
    next(payload for payload in guild if payload["name"] == "history")["description"] = "someone else edited this"
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    sync.assert_not_awaited()


def test_update_refuses_the_reviewed_change_when_any_other_field_also_differs(client, settings, monkeypatch):
    """@description: The reviewed description change is not a blanket exemption: any other drift in the same guild stops the update.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = remote_with_reviewed_description(client)
    next(payload for payload in guild if payload["name"] == "share")["description"] = "unreviewed share text"
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    sync.assert_not_awaited()


def test_update_refuses_the_reviewed_change_when_history_has_an_unreviewed_parameter_difference(client, settings, monkeypatch):
    """@description: An unreviewed parameter difference inside history stops the update even though its description change is reviewed.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = remote_with_reviewed_description(client)
    history = next(payload for payload in guild if payload["name"] == "history")
    history.setdefault("options", []).append({"name": "extra", "type": 3, "description": "unreviewed"})
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    sync.assert_not_awaited()


def test_inspect_reports_only_the_reviewed_change_as_approved_and_no_unreviewed_difference(client, settings, monkeypatch):
    """@description: Inspection lists the raw difference and marks it reviewed, leaving no unreviewed difference.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    stub_remote(client, monkeypatch, guild_commands=remote_with_reviewed_description(client), global_commands=[])
    summary = _run(inspect_remote_commands(client, settings))
    assert summary["guild_five_matches_local"] is False
    assert len(summary["guild_five_differences"]) == 1
    assert summary["guild_unreviewed_differences"] == []


# ---- owner visibility: reviewed owner permission change and hidden remote fields ----

def owner_payload(client) -> dict:
    """@description: Return the local Owner group payload as it would be published.
    @param client: Synthetic transport client.
    @return: Owner group payload.
    """
    return next(payload for payload in local_owner_payloads(client, GUILD_ID))


def test_owner_group_is_hidden_from_members_by_default_and_the_five_global_commands_are_unchanged(client):
    """@description: The Owner group defaults to no member permission, while the five global commands keep their definitions.
    @param client: Synthetic transport client.
    @return: None.
    """
    assert owner_payload(client)["default_member_permissions"] == 0
    for payload in local_five_payloads(client):
        assert payload["default_member_permissions"] is None
        assert payload.get("dm_permission", True) is True


def test_remote_contract_reads_default_permissions_dm_and_nsfw_from_the_remote_object_not_only_to_dict(client, settings, monkeypatch):
    """@description: A remote permission that to_dict omits is still compared, so the contract is not blind to it.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = local_five_payloads(client)
    next(payload for payload in guild if payload["name"] == "share")["default_member_permissions"] = 8
    stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    summary = _run(inspect_remote_commands(client, settings))
    assert any("default_member_permissions" in line for line in summary["guild_five_differences"])


def owner_remote_with(client, **changes) -> list[dict]:
    """@description: Build the remote guild as it stands after the first update: five reviewed commands plus Owner with the old permission.
    @param client: Synthetic transport client.
    @param changes: Field overrides applied to the remote Owner payload.
    @return: Remote guild payloads.
    """
    owner = owner_payload(client)
    owner["default_member_permissions"] = None  # [observed old value]: the first update published the Owner group open to members.
    owner.update(changes)
    return local_five_payloads(client) + [owner]


def test_update_accepts_exactly_the_reviewed_owner_permission_change_from_open_to_hidden(client, settings, monkeypatch):
    """@description: The reviewed owner permission change (open to hidden) is accepted and the published result matches the full six.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = owner_remote_with(client)
    six = expected_six_contract(client, GUILD_ID)
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    sync.side_effect = owner_sync_publisher(guild, six)
    summary = _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    assert summary["status"] == "published_matches_expected"
    sync.assert_awaited_once()


def test_update_refuses_any_other_owner_difference_even_when_the_permission_change_is_reviewed(client, settings, monkeypatch):
    """@description: The reviewed permission change is not a blanket exemption for the Owner group: another drift stops the update.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    sync = stub_remote(client, monkeypatch, guild_commands=owner_remote_with(client, description="someone changed this"),
                       global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    sync.assert_not_awaited()


def test_update_refuses_an_owner_permission_that_is_neither_the_reviewed_old_nor_the_new_value(client, settings, monkeypatch):
    """@description: An owner permission other than the reviewed old or new value is unexpected and stops the update.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    sync = stub_remote(client, monkeypatch, guild_commands=owner_remote_with(client, default_member_permissions=8),
                       global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    sync.assert_not_awaited()


def test_update_applies_the_history_and_owner_reviewed_changes_together_and_shows_both(client, settings, monkeypatch):
    """@description: Both reviewed changes are shown in the prompt and applied in one publish, with the full result verified.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    guild = owner_remote_with(client)
    next(payload for payload in guild if payload["name"] == "history")["description"] = REMOTE_HISTORY_DESCRIPTION
    six = expected_six_contract(client, GUILD_ID)
    sync = stub_remote(client, monkeypatch, guild_commands=guild, global_commands=[])
    sync.side_effect = owner_sync_publisher(guild, six)
    prompts = []

    def confirm(prompt: str) -> str:
        """@description: Record the prompt and type the correct confirmation.
        @param prompt: Prompt text.
        @return: The correct typed confirmation.
        """
        prompts.append(prompt)
        return "UPDATE 5005 2002"

    summary = _run(update_guild_sync(client, settings, authorized=True, confirm=confirm))
    assert summary["status"] == "published_matches_expected"
    assert "owner.default_member_permissions" in prompts[0] and "history.description" in prompts[0]


def test_remote_already_at_the_new_owner_setting_is_recognized_without_a_second_write(client, settings, monkeypatch):
    """@description: When the remote guild already holds the reviewed six, the update recognizes it and publishes nothing again.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    sync = stub_remote(client, monkeypatch, guild_commands=owner_remote_with(client, default_member_permissions=0),
                       global_commands=[])
    summary = _run(update_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "UPDATE 5005 2002"))
    assert summary["status"] == "already_matches"
    sync.assert_not_awaited()


def test_listen_refuses_while_the_owner_group_still_holds_the_old_open_permission(client, settings, monkeypatch):
    """@description: Listen refuses to connect while the Owner group is still open to members, so the bot never runs in that state.
    @param client: Synthetic transport client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    stub_remote(client, monkeypatch, guild_commands=owner_remote_with(client), global_commands=[])
    with pytest.raises(AcceptanceError):
        _run(listen_owner_transport(client, settings, authorized=True))
    client.connect.assert_not_awaited()
