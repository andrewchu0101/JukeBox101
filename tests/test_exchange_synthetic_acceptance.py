"""Verify the labelled synthetic /exchange acceptance entry: scripted outcomes, argument refusals and the transport delegation.

Offline only: fake database, scripted DM outcomes and a silent reply sink; no Discord, network, environment file, MySQL or real points.
"""

import pytest

from tests import exchange_synthetic_acceptance as entry


def scenarios(injection: str) -> dict:
    """@description: Run the offline scenarios for one scripted mode and index them by label.
    @param injection: Scripted outcome mode.
    @return: Summaries keyed by scenario label.
    """
    return {item["scenario"]: item for item in entry.run_offline_scenarios(injection)}


def test_the_success_scenario_delivers_and_the_zero_point_user_is_refused_without_debit() -> None:
    """@description: A requester with a point is delivered once; a zero-point user gets the reviewed insufficient-credits text.
    @param: none
    @return: None.
    """
    result = scenarios("success")
    assert result["requester_with_point"]["delivery"] == "delivered" and result["requester_with_point"]["labelled"]
    assert result["zero_point_user"]["reply"] == "【合成測試】點數不足，交換一首歌曲需要 1 Nickel。"


def test_an_injected_uncertain_outcome_is_not_refunded() -> None:
    """@description: The injected uncertain outcome stays uncertain with no refund.
    @param: none
    @return: None.
    """
    result = scenarios("uncertain")["requester_with_point"]
    assert result["delivery"] == "uncertain" and result["refund"] is None


def test_an_injected_not_sent_outcome_is_refunded() -> None:
    """@description: The injected proven-unsent outcome refunds the point and says so.
    @param: none
    @return: None.
    """
    result = scenarios("not_sent")["requester_with_point"]
    assert result["delivery"] == "failed" and result["refund"] == "refunded" and "已退回 1 點" in result["reply"]


def test_an_injected_refund_failure_is_reported_as_pending_and_never_as_refunded() -> None:
    """@description: A failed refund transaction is reported as pending, not as returned.
    @param: none
    @return: None.
    """
    result = scenarios("refund_fail")["requester_with_point"]
    assert result["refund"] == "pending" and "已退回" not in result["reply"] and "退款待處理" in result["reply"]


def test_the_scenario_mode_refuses_every_network_switch() -> None:
    """@description: Scenario mode never accepts gateway or live switches.
    @param: none
    @return: None.
    """
    with pytest.raises(RuntimeError):
        entry.validate_arguments(entry.argument_parser().parse_args(["scenario", "--authorize-gateway"]))
    with pytest.raises(RuntimeError):
        entry.validate_arguments(entry.argument_parser().parse_args(["scenario", "--authorize-live-dm"]))


def test_listen_requires_the_gateway_switch_and_a_live_switch_before_any_real_dm() -> None:
    """@description: Listen needs the gateway switch, and the default scripted outcome never silently becomes a live DM.
    @param: none
    @return: None.
    """
    with pytest.raises(RuntimeError):
        entry.validate_arguments(entry.argument_parser().parse_args(["listen"]))
    with pytest.raises(RuntimeError):
        entry.validate_arguments(entry.argument_parser().parse_args(["listen", "--authorize-gateway"]))
    entry.validate_arguments(entry.argument_parser().parse_args(["listen", "--authorize-gateway", "--inject", "not_sent"]))


def test_listen_is_not_wired_in_this_round(capsys: pytest.CaptureFixture) -> None:
    """@description: The entry refuses to run listen until its wiring is reviewed, so no Discord connection can start.
    @param capsys: Captured output.
    @return: None.
    """
    with pytest.raises(RuntimeError, match="not part of this offline round"):
        entry.main(["listen", "--authorize-gateway", "--inject", "not_sent"])


def test_scenario_main_prints_the_synthetic_notice_and_labelled_results(capsys: pytest.CaptureFixture) -> None:
    """@description: The offline run prints the synthetic notice before any result.
    @param capsys: Captured output.
    @return: None.
    """
    assert entry.main(["scenario", "--inject", "success"]) == 0
    output = capsys.readouterr().out
    assert output.startswith(entry.SYNTHETIC_NOTICE) and "requester_with_point" in output


def test_the_composite_routes_exchange_to_the_labelled_flow_and_delegates_the_rest() -> None:
    """@description: The handler sees the synthetic route, while every other operation comes from the transport-only service.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("success")
    assert service.synthetic_exchange_enabled is True
    assert callable(service.share) and callable(service.balance)
