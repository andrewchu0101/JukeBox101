"""Verify the trust-mode switch: allowlist (today's behaviour) by default, music_category only beside every protection it depends on.

music_category drops the requirement that the channel is on the Owner's allowlist, so automatic review, API retention and the automatic
version comparison must all be on; otherwise a duplicate song would have nobody checking it. Every other value refuses to start.
"""

import pytest

from bot.auto_startup import build_auto_pipeline, load_auto_settings
from bot.config import (AUTO_TRUST_MODE_ENV, TRUST_MODE_ALLOWLIST, TRUST_MODE_MUSIC_CATEGORY, ConfigurationError, load_auto_trust_mode)

FULL = {"JUKEBOX_AUTO_REVIEW": "on", "JUKEBOX_API_RETENTION": "on", "JUKEBOX_AUTO_VERSION_CONFIRM": "on", "YOUTUBE_API_KEY": "synthetic-key"}
CHANNEL = "UC" + "a" * 22


def test_the_setting_name_and_the_two_legal_values_are_fixed() -> None:
    """@description: Lock the names so a rename is a visible, reviewed change.
    @param: none
    @return: None.
    """
    assert AUTO_TRUST_MODE_ENV == "JUKEBOX_AUTO_TRUST_MODE"
    assert (TRUST_MODE_ALLOWLIST, TRUST_MODE_MUSIC_CATEGORY) == ("allowlist", "music_category")


def test_the_default_is_allowlist_and_changes_nothing_else() -> None:
    """@description: An unset setting is today's behaviour in every combination.
    @param: none
    @return: None.
    """
    assert load_auto_trust_mode({}) == TRUST_MODE_ALLOWLIST
    assert load_auto_settings({}).trust_mode == TRUST_MODE_ALLOWLIST
    assert load_auto_settings(dict(FULL)).trust_mode == TRUST_MODE_ALLOWLIST


@pytest.mark.parametrize("value,expected", [("allowlist", "allowlist"), ("music_category", "music_category"), (" Music_Category ", "music_category"),
                                            ("ALLOWLIST", "allowlist")])
def test_both_legal_values_parse_case_insensitively_after_trimming(value, expected) -> None:
    """@description: The loader follows the same normalisation as the other switches.
    @param value: Raw setting value.
    @param expected: Normalised mode.
    @return: None.
    """
    assert load_auto_trust_mode({AUTO_TRUST_MODE_ENV: value}) == expected


@pytest.mark.parametrize("value", ["", "on", "1", "true", "category", "music", "any", "open", "music-category", "allow list", "maybe"])
def test_any_other_value_refuses_to_start_even_when_every_other_mode_is_off(value) -> None:
    """@description: A typo must never silently select the open mode, and must be refused whether or not review is on.
    @param value: Invalid mode.
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        load_auto_trust_mode({AUTO_TRUST_MODE_ENV: value})
    with pytest.raises(ConfigurationError):
        load_auto_settings({AUTO_TRUST_MODE_ENV: value})


@pytest.mark.parametrize("missing", ["JUKEBOX_AUTO_REVIEW", "JUKEBOX_API_RETENTION", "JUKEBOX_AUTO_VERSION_CONFIRM"])
def test_music_category_refuses_to_start_when_any_protection_it_depends_on_is_off(missing) -> None:
    """@description: Without review, retention or the version comparison nobody would check duplicates, so the open mode may not start.
    @param missing: Prerequisite turned off.
    @return: None.
    """
    environment = dict(FULL, **{AUTO_TRUST_MODE_ENV: "music_category"})
    environment[missing] = "off"
    with pytest.raises(ConfigurationError):
        load_auto_settings(environment)


def test_music_category_with_everything_on_is_accepted_with_an_empty_allowlist() -> None:
    """@description: The open mode needs no allowlist at all.
    @param: none
    @return: None.
    """
    settings = load_auto_settings(dict(FULL, **{AUTO_TRUST_MODE_ENV: "music_category"}))
    assert settings.trust_mode == TRUST_MODE_MUSIC_CATEGORY and settings.trust_mode == "music_category"
    assert settings.trusted_channel_ids == frozenset()


def test_music_category_may_still_carry_an_allowlist_without_error() -> None:
    """@description: An existing seed list is harmless; it is simply not required.
    @param: none
    @return: None.
    """
    environment = dict(FULL, **{AUTO_TRUST_MODE_ENV: "music_category", "YOUTUBE_TRUSTED_CHANNEL_IDS": CHANNEL})
    assert load_auto_settings(environment).trusted_channel_ids == frozenset({CHANNEL})


def test_allowlist_mode_still_starts_with_an_empty_list_and_without_version_confirmation() -> None:
    """@description: The current rules are unchanged: an empty list is legal and approves nothing; the comparison is optional.
    @param: none
    @return: None.
    """
    environment = {"JUKEBOX_AUTO_REVIEW": "on", "JUKEBOX_API_RETENTION": "on", "YOUTUBE_API_KEY": "synthetic-key", AUTO_TRUST_MODE_ENV: "allowlist"}
    settings = load_auto_settings(environment)
    assert settings.trust_mode == "allowlist" and settings.trusted_channel_ids == frozenset() and settings.version_confirm is False


def test_the_refusal_names_the_setting_and_never_echoes_a_value() -> None:
    """@description: Startup errors must be actionable and secret-free.
    @param: none
    @return: None.
    """
    with pytest.raises(ConfigurationError) as invalid:
        load_auto_trust_mode({AUTO_TRUST_MODE_ENV: "synthetic-value-shaped-like-a-secret"})
    assert AUTO_TRUST_MODE_ENV in str(invalid.value) and "synthetic-value" not in str(invalid.value)
    with pytest.raises(ConfigurationError) as missing:
        load_auto_settings({AUTO_TRUST_MODE_ENV: "music_category"})
    assert AUTO_TRUST_MODE_ENV in str(missing.value)


def test_the_representation_shows_the_mode_but_never_the_key() -> None:
    """@description: Settings may be logged.
    @param: none
    @return: None.
    """
    text = repr(load_auto_settings(dict(FULL, **{AUTO_TRUST_MODE_ENV: "music_category"})))
    assert "trust_mode=music_category" in text and "synthetic-key" not in text


def test_the_pipeline_builder_passes_the_mode_to_the_pipeline() -> None:
    """@description: Only the validated settings value reaches the pipeline.
    @param: none
    @return: None.
    """
    assert build_auto_pipeline(None, load_auto_settings(dict(FULL)))._trust_mode == "allowlist"
    assert build_auto_pipeline(None, load_auto_settings(dict(FULL, **{AUTO_TRUST_MODE_ENV: "music_category"})))._trust_mode == "music_category"
