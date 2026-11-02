"""Verify the CI runtime policy proves the published image defaults to every automatic mode closed.

The policy runs the real closed routes without a login or connection; here the environment is varied to prove that a stray switch is caught.
"""

import asyncio

import pytest

from bot.config import ConfigurationError
from bot.runtime_policy import check_policy

AUTO_SWITCHES = ('JUKEBOX_AUTO_REVIEW', 'JUKEBOX_API_RETENTION', 'JUKEBOX_AUTO_SETTLEMENT', 'JUKEBOX_AUTO_VERSION_CONFIRM')


@pytest.fixture
def closed_environment(monkeypatch):
    """@description: Model the CI image environment: legacy switches explicitly off, automatic modes unset.
    @param monkeypatch: Environment isolation.
    @return: The monkeypatch.
    """
    for name in AUTO_SWITCHES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('JUKEBOX_FORMAL_EXCHANGE', 'off')
    monkeypatch.setenv('JUKEBOX_NICKEL_CLAIM', 'off')
    return monkeypatch


def test_the_default_environment_passes_and_reports_every_automatic_mode_off(closed_environment) -> None:
    """@description: The receipt kept as CI evidence must say the automatic modes are off.
    @param closed_environment: CI-like environment.
    @return: None.
    """
    receipt = asyncio.run(check_policy())
    assert receipt['result'] == 'passed'
    assert receipt['auto_modes'] == {name: 'off' for name in AUTO_SWITCHES}


@pytest.mark.parametrize('switch', AUTO_SWITCHES)
@pytest.mark.parametrize('value', ['on', 'true', '1'])
def test_any_automatic_switch_turned_on_fails_the_policy(closed_environment, switch, value) -> None:
    """@description: A published image must be provably closed by default, so a stray enabling value is a CI failure.
    @param closed_environment: CI-like environment.
    @param switch: Automatic switch.
    @param value: Enabling value.
    @return: None.
    """
    closed_environment.setenv(switch, value)
    with pytest.raises(ConfigurationError):
        asyncio.run(check_policy())


@pytest.mark.parametrize('switch', AUTO_SWITCHES)
def test_an_unrecognised_automatic_switch_value_fails_the_policy(closed_environment, switch) -> None:
    """@description: A typo is not silently treated as off in the CI proof.
    @param closed_environment: CI-like environment.
    @param switch: Automatic switch.
    @return: None.
    """
    closed_environment.setenv(switch, 'maybe')
    with pytest.raises(ConfigurationError):
        asyncio.run(check_policy())
