"""Verify the container health check for the trust mode: a complete open-mode configuration is healthy, any missing protection is not.

Readiness and Gateway evidence are faked; no database, Discord login or YouTube call is made.
"""

import pytest

from bot import healthcheck
from tests.test_healthcheck_auto_modes import KEY_CANARY, enable, healthy_environment  # noqa: F401  (fixture imported by name)

MODE = "JUKEBOX_AUTO_TRUST_MODE"
OPEN = dict(JUKEBOX_AUTO_REVIEW="on", JUKEBOX_API_RETENTION="on", JUKEBOX_AUTO_VERSION_CONFIRM="on", YOUTUBE_API_KEY=KEY_CANARY, **{MODE: "music_category"})


def test_the_open_mode_with_every_prerequisite_and_no_allowlist_is_ready(healthy_environment) -> None:  # noqa: F811
    """@description: Turning the mode on correctly is a legal state, and an empty allowlist is fine in this mode.
    @param healthy_environment: Healthy environment.
    @return: None.
    """
    enable(healthy_environment, **OPEN)
    summary = healthcheck.check_health()
    assert (summary["result"], summary["problems"]) == ("ready", [])


@pytest.mark.parametrize("missing", ["JUKEBOX_AUTO_REVIEW", "JUKEBOX_API_RETENTION", "JUKEBOX_AUTO_VERSION_CONFIRM", "YOUTUBE_API_KEY"])
def test_the_open_mode_without_a_protection_it_depends_on_is_not_ready(healthy_environment, missing) -> None:  # noqa: F811
    """@description: Each missing prerequisite fails closed through the dependency-failure path.
    @param healthy_environment: Healthy environment.
    @param missing: Prerequisite removed.
    @return: None.
    """
    settings = dict(OPEN)
    settings.pop(missing)
    enable(healthy_environment, **settings)
    summary = healthcheck.check_health()
    assert summary["result"] == "not_ready" and summary["problems"] == ["HEALTH_DEPENDENCY_FAILED"] and summary["error_class"] == "ConfigurationError"


@pytest.mark.parametrize("value", ["", "open", "music-category", "maybe", "1"])
def test_an_invalid_mode_is_not_ready(healthy_environment, value) -> None:  # noqa: F811
    """@description: A typo is never treated as the default.
    @param healthy_environment: Healthy environment.
    @param value: Invalid mode.
    @return: None.
    """
    enable(healthy_environment, **{MODE: value})
    assert healthcheck.check_health()["result"] == "not_ready"


def test_allowlist_mode_stays_ready_exactly_as_before(healthy_environment) -> None:  # noqa: F811
    """@description: The default and the explicit allowlist value change nothing.
    @param healthy_environment: Healthy environment.
    @return: None.
    """
    assert healthcheck.check_health()["result"] == "ready"
    enable(healthy_environment, **{MODE: "allowlist"})
    assert healthcheck.check_health()["result"] == "ready"


def test_health_output_in_the_open_mode_contains_no_key(healthy_environment, capsys) -> None:  # noqa: F811
    """@description: The container log exposes this output.
    @param healthy_environment: Healthy environment.
    @param capsys: Output capture.
    @return: None.
    """
    enable(healthy_environment, **OPEN)
    assert healthcheck.main() == 0
    assert KEY_CANARY not in capsys.readouterr().out
