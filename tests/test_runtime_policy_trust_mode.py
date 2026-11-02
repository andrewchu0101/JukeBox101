"""Verify the CI runtime policy proves the published image uses the allowlist trust mode, so the open music_category mode cannot ship by default.

The policy runs the real closed routes without a login or connection; here the environment is varied to prove a stray mode value is caught.
"""

import asyncio

import pytest

from bot.config import ConfigurationError
from bot.runtime_policy import check_policy

MODE = "JUKEBOX_AUTO_TRUST_MODE"
AUTO_SWITCHES = ("JUKEBOX_AUTO_REVIEW", "JUKEBOX_API_RETENTION", "JUKEBOX_AUTO_SETTLEMENT", "JUKEBOX_AUTO_VERSION_CONFIRM")


@pytest.fixture
def closed_environment(monkeypatch):
    """@description: Model the CI image environment: legacy switches off, automatic modes and trust mode unset.
    @param monkeypatch: Environment isolation.
    @return: The monkeypatch.
    """
    for name in (*AUTO_SWITCHES, MODE):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JUKEBOX_FORMAL_EXCHANGE", "off")
    monkeypatch.setenv("JUKEBOX_NICKEL_CLAIM", "off")
    return monkeypatch


@pytest.mark.parametrize("value", [None, "allowlist"])
def test_an_unset_or_allowlist_mode_passes_and_the_receipt_says_allowlist(closed_environment, value) -> None:
    """@description: The receipt kept as CI evidence must state the trust mode.
    @param closed_environment: CI-like environment.
    @param value: Unset or the default value.
    @return: None.
    """
    if value is not None:
        closed_environment.setenv(MODE, value)
    receipt = asyncio.run(check_policy())
    assert receipt["result"] == "passed"
    assert receipt["auto_modes"] == {**{name: "off" for name in AUTO_SWITCHES}, MODE: "allowlist"}


@pytest.mark.parametrize("value", ["music_category", "MUSIC_CATEGORY", "maybe", "", "on"])
def test_the_open_mode_or_any_invalid_value_fails_the_policy(closed_environment, value) -> None:
    """@description: A published image must be provably on the allowlist mode, so music_category and typos are a CI failure.
    @param closed_environment: CI-like environment.
    @param value: Open mode or invalid value.
    @return: None.
    """
    closed_environment.setenv(MODE, value)
    with pytest.raises(ConfigurationError):
        asyncio.run(check_policy())
