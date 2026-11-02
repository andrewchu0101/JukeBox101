"""Verify the container health check no longer treats the formal /exchange switch as a fault, while the Nickel claim switch still is one.

Readiness and Gateway evidence are faked; no database, Discord login or network is used. The controlled compose file and the runtime policy keep forcing
the switch off; those stay covered by their own tests and are not changed here.
"""

import pytest

from bot import healthcheck
from tests.test_healthcheck_auto_modes import healthy_environment  # noqa: F401  (pytest fixture imported by name)

ON_VALUES = ['on', '1', 'true', 'yes']


@pytest.mark.parametrize('value', ON_VALUES)
def test_the_formal_exchange_switch_on_is_not_reported_as_a_feature_policy_fault(healthy_environment, value) -> None:
    """@description: Opening /exchange in stage B must not make the container unhealthy.
    @param healthy_environment: Healthy environment with every switch off.
    @param value: Accepted spelling of on.
    @return: None.
    """
    healthy_environment.setenv('JUKEBOX_FORMAL_EXCHANGE', value)
    summary = healthcheck.check_health()
    assert 'FEATURE_POLICY_ENABLED' not in summary['problems'] and summary['result'] == 'ready'


def test_the_nickel_claim_switch_on_is_still_a_feature_policy_fault(healthy_environment) -> None:
    """@description: Only the exchange switch is relaxed; /claim stays closed by policy.
    @param healthy_environment: Healthy environment with every switch off.
    @return: None.
    """
    healthy_environment.setenv('JUKEBOX_NICKEL_CLAIM', 'on')
    summary = healthcheck.check_health()
    assert summary['result'] == 'not_ready' and 'FEATURE_POLICY_ENABLED' in summary['problems']


def test_both_switches_on_still_reports_the_claim_fault(healthy_environment) -> None:
    """@description: Turning exchange on does not hide an enabled claim switch.
    @param healthy_environment: Healthy environment with every switch off.
    @return: None.
    """
    healthy_environment.setenv('JUKEBOX_FORMAL_EXCHANGE', 'on')
    healthy_environment.setenv('JUKEBOX_NICKEL_CLAIM', 'on')
    assert 'FEATURE_POLICY_ENABLED' in healthcheck.check_health()['problems']


def test_an_invalid_exchange_switch_value_still_fails_closed(healthy_environment) -> None:
    """@description: A typo in the switch is a configuration error, never silently healthy.
    @param healthy_environment: Healthy environment with every switch off.
    @return: None.
    """
    healthy_environment.setenv('JUKEBOX_FORMAL_EXCHANGE', 'maybe')
    summary = healthcheck.check_health()
    assert summary['result'] == 'not_ready' and summary['problems'] == ['HEALTH_DEPENDENCY_FAILED']
