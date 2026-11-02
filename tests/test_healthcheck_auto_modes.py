"""Verify the container health check treats a correctly configured automatic mode as healthy and any misconfiguration as unhealthy.

Readiness and Gateway evidence are faked; no database, Discord login or YouTube call is made. Automatic modes are optional features, not faults,
but a switched-on mode with a bad value, a missing prerequisite or a missing credential must fail closed.
"""

import json

import pytest

from bot import healthcheck

APPLICATION = 789001
READY_DATABASE = {'result': 'ready', 'problems': [], 'account': 'jukebox101_app', 'database': 'jukebox101', 'tls_mode': 'verify-full'}
CHANNEL = 'UC' + 'a' * 22
KEY_CANARY = 'synthetic-key-canary-7391'  # [leak marker]: Must never appear in health output.
AUTO_NAMES = ('JUKEBOX_AUTO_REVIEW', 'JUKEBOX_API_RETENTION', 'JUKEBOX_AUTO_SETTLEMENT', 'JUKEBOX_AUTO_VERSION_CONFIRM',
              'YOUTUBE_TRUSTED_CHANNEL_IDS', 'YOUTUBE_API_KEY', 'YOUTUBE_API_KEY_FILE')


@pytest.fixture
def healthy_environment(monkeypatch):
    """@description: Start from a fully healthy, automatic-modes-off environment with faked readiness and Gateway.
    @param monkeypatch: Environment and dependency isolation.
    @return: The monkeypatch, so a test can switch modes on.
    """
    for name in AUTO_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in dict(DISCORD_EXPECTED_APPLICATION_ID=str(APPLICATION), JUKEBOX_FORMAL_EXCHANGE='off', JUKEBOX_NICKEL_CLAIM='off',
                            DB_HOST='db.example.internal', DB_USER='jukebox101_app', DB_NAME='jukebox101').items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(healthcheck, 'check_readiness', lambda: dict(READY_DATABASE))
    monkeypatch.setattr(healthcheck, 'check_gateway', lambda _: {'result': 'ready', 'problems': []})
    return monkeypatch


def enable(monkeypatch, **settings) -> None:
    """@description: Set automatic-mode variables for one test.
    @param monkeypatch: Environment isolation.
    @param settings: Variable names and values.
    @return: None.
    """
    for name, value in settings.items():
        monkeypatch.setenv(name, value)


FULL_ON = dict(JUKEBOX_AUTO_REVIEW='on', JUKEBOX_API_RETENTION='on', YOUTUBE_API_KEY=KEY_CANARY, YOUTUBE_TRUSTED_CHANNEL_IDS=CHANNEL)


def test_all_automatic_modes_off_is_ready(healthy_environment) -> None:
    """@description: The default deployment stays healthy.
    @param healthy_environment: Healthy environment.
    @return: None.
    """
    assert healthcheck.check_health()['result'] == 'ready'


def test_review_and_retention_on_with_a_complete_configuration_is_ready(healthy_environment) -> None:
    """@description: Turning the feature on correctly must not be reported as a fault.
    @param healthy_environment: Healthy environment.
    @return: None.
    """
    enable(healthy_environment, **FULL_ON)
    summary = healthcheck.check_health()
    assert (summary['result'], summary['problems']) == ('ready', [])


def test_version_confirmation_on_with_review_and_retention_is_ready(healthy_environment) -> None:
    """@description: The new independent switch is a legal state beside review and retention.
    @param healthy_environment: Healthy environment.
    @return: None.
    """
    enable(healthy_environment, JUKEBOX_AUTO_VERSION_CONFIRM='on', **FULL_ON)
    assert healthcheck.check_health()['result'] == 'ready'


@pytest.mark.parametrize('settings', [
    dict(JUKEBOX_AUTO_REVIEW='on', JUKEBOX_API_RETENTION='on'),
    dict(JUKEBOX_AUTO_REVIEW='on', YOUTUBE_API_KEY=KEY_CANARY),
    dict(JUKEBOX_AUTO_REVIEW='maybe'), dict(JUKEBOX_API_RETENTION='2'), dict(JUKEBOX_AUTO_SETTLEMENT='yes please'),
    dict(JUKEBOX_AUTO_VERSION_CONFIRM='onn', **FULL_ON), dict(JUKEBOX_AUTO_VERSION_CONFIRM='on', JUKEBOX_API_RETENTION='on'),
    dict(YOUTUBE_TRUSTED_CHANNEL_IDS='@handle'), dict(YOUTUBE_TRUSTED_CHANNEL_IDS='UCshort'),
    dict(JUKEBOX_AUTO_REVIEW='on', JUKEBOX_API_RETENTION='on', YOUTUBE_API_KEY_FILE='/nonexistent/youtube_api_key')])
def test_a_bad_value_missing_prerequisite_or_missing_key_is_not_ready(healthy_environment, settings) -> None:
    """@description: Every misconfiguration fails closed through the dependency-failure path.
    @param healthy_environment: Healthy environment.
    @param settings: Misconfigured variables.
    @return: None.
    """
    enable(healthy_environment, **settings)
    summary = healthcheck.check_health()
    assert summary['result'] == 'not_ready' and summary['problems'] == ['HEALTH_DEPENDENCY_FAILED']
    assert summary['error_class'] == 'ConfigurationError'


def test_an_empty_allowlist_with_review_on_is_ready_because_it_approves_nothing(healthy_environment) -> None:
    """@description: An empty allowlist is the safe state, so it is not a health fault.
    @param healthy_environment: Healthy environment.
    @return: None.
    """
    enable(healthy_environment, JUKEBOX_AUTO_REVIEW='on', JUKEBOX_API_RETENTION='on', YOUTUBE_API_KEY=KEY_CANARY)
    assert healthcheck.check_health()['result'] == 'ready'


@pytest.mark.parametrize('legacy', ['JUKEBOX_NICKEL_CLAIM'])  # [Owner ruling, stage B]: JUKEBOX_FORMAL_EXCHANGE=on is a legal state; see tests/test_healthcheck_formal_exchange.py.
def test_legacy_feature_switches_stay_unhealthy_when_on_even_with_review_on(healthy_environment, legacy) -> None:
    """@description: Allowing automatic review must not relax the closed claim route.
    @param healthy_environment: Healthy environment.
    @param legacy: Legacy switch turned on.
    @return: None.
    """
    enable(healthy_environment, **FULL_ON)
    healthy_environment.setenv(legacy, 'on')
    summary = healthcheck.check_health()
    assert summary['result'] == 'not_ready' and 'FEATURE_POLICY_ENABLED' in summary['problems']


def test_the_database_settlement_flag_must_still_agree_with_the_mode(healthy_environment) -> None:
    """@description: A readiness problem is still reported while review is on.
    @param healthy_environment: Healthy environment.
    @return: None.
    """
    enable(healthy_environment, **FULL_ON)
    healthy_environment.setattr(healthcheck, 'check_readiness', lambda: dict(READY_DATABASE, result='not_ready', problems=['SETTLEMENT_SWITCH_OFF']))
    summary = healthcheck.check_health()
    assert summary['result'] == 'not_ready' and 'SETTLEMENT_SWITCH_OFF' in summary['problems']


def test_health_output_never_contains_the_key_or_the_allowlist(healthy_environment, capsys) -> None:
    """@description: The container log and Docker inspect expose this output.
    @param healthy_environment: Healthy environment.
    @param capsys: Output capture.
    @return: None.
    """
    enable(healthy_environment, **FULL_ON)
    assert healthcheck.main() == 0
    printed = capsys.readouterr().out
    assert json.loads(printed)['result'] == 'ready' and KEY_CANARY not in printed and CHANNEL not in printed


def test_a_failing_configuration_prints_no_key_either(healthy_environment, capsys) -> None:
    """@description: The failure path must be as quiet as the success path.
    @param healthy_environment: Healthy environment.
    @param capsys: Output capture.
    @return: None.
    """
    enable(healthy_environment, JUKEBOX_AUTO_REVIEW='on', YOUTUBE_API_KEY=KEY_CANARY)
    assert healthcheck.main() == 1
    assert KEY_CANARY not in capsys.readouterr().out


def test_the_health_check_makes_no_youtube_call(healthy_environment, monkeypatch) -> None:
    """@description: A health probe every ten seconds must cost no API quota.
    @param healthy_environment: Healthy environment.
    @param monkeypatch: Dependency isolation.
    @return: None.
    """
    from bot import youtube_metadata

    def forbidden(*_arguments, **_keywords):
        raise AssertionError('health check called the YouTube API')
    monkeypatch.setattr(youtube_metadata, 'http_transport', forbidden)
    enable(healthy_environment, **FULL_ON)
    assert healthcheck.check_health()['result'] == 'ready'
