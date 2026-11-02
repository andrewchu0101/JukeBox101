"""Verify the independent switch for automatic version confirmation: off by default, strict values, valid only beside review and retention."""

import pytest

from bot.auto_startup import load_auto_settings
from bot.config import ConfigurationError

SWITCH = "JUKEBOX_AUTO_VERSION_CONFIRM"
BASE = {"JUKEBOX_AUTO_REVIEW": "on", "JUKEBOX_API_RETENTION": "on", "YOUTUBE_API_KEY": "synthetic-key"}


def test_version_confirmation_is_off_by_default_and_changes_nothing_else() -> None:
    """@description: An unset switch is off in every combination.
    @param: none
    @return: None.
    """
    assert load_auto_settings({}).version_confirm is False
    assert load_auto_settings(dict(BASE)).version_confirm is False


@pytest.mark.parametrize("value", ["maybe", "2", "enable", "onn", "tru e"])
def test_an_unrecognised_value_refuses_to_start(value) -> None:
    """@description: A typo must close the route by refusing startup, never open it.
    @param value: Invalid switch value.
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        load_auto_settings(dict(BASE, **{SWITCH: value}))


@pytest.mark.parametrize("missing", ["JUKEBOX_AUTO_REVIEW", "JUKEBOX_API_RETENTION"])
def test_turning_it_on_requires_review_and_retention(missing) -> None:
    """@description: Comparison needs lookups, and lookups need the retention guarantee.
    @param missing: Prerequisite turned off.
    @return: None.
    """
    environment = dict(BASE, **{SWITCH: "on"})
    environment[missing] = "off"
    with pytest.raises(ConfigurationError):
        load_auto_settings(environment)


def test_turning_it_on_with_review_and_retention_is_accepted() -> None:
    """@description: The valid combination.
    @param: none
    @return: None.
    """
    assert load_auto_settings(dict(BASE, **{SWITCH: "on"})).version_confirm is True


def test_the_refusal_names_the_setting_and_never_echoes_a_value() -> None:
    """@description: Startup errors must be actionable and secret-free.
    @param: none
    @return: None.
    """
    with pytest.raises(ConfigurationError) as error:
        load_auto_settings(dict(BASE, **{SWITCH: "synthetic-value-shaped-like-a-secret"}))
    assert SWITCH in str(error.value) and "synthetic-value" not in str(error.value)


def test_the_representation_shows_the_mode_but_never_the_key() -> None:
    """@description: Settings may be logged.
    @param: none
    @return: None.
    """
    text = repr(load_auto_settings(dict(BASE, **{SWITCH: "on"})))
    assert "version_confirm=True" in text and "synthetic-key" not in text
