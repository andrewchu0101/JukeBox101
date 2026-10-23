"""Verify the offline Owner review demo through its non-interactive script mode.

Each scenario builds a fresh synthetic session over the real repository and the in-memory SQL fake; no MySQL, Discord or Gateway is contacted.
"""

import re

from tests.owner_review_demo import OWNER_ID, new_session, run_script

STRANGER_ID = 4242  # [non-owner identity]: Any Discord identity other than the configured Owner.
OPERATIONS = ("op-1", "op-2", "op-3", "op-4", "op-5")
TOKEN_PATTERN = re.compile(r"token=([A-Za-z0-9_-]+)")  # [token shape]: Stop before any Chinese text on the same line.


def token_from(lines: list[str]) -> str:
    """@description: Extract the single confirmation token printed by a preview.
    @param lines: Output lines returned by one demo command.
    @return: Opaque token identifier shown to the Owner.
    """
    matches = [TOKEN_PATTERN.search(line) for line in lines]
    return next(match.group(1) for match in matches if match is not None)


def operation_row(lines: list[str], operation_id: str) -> str:
    """@description: Find the list row that describes one synthetic operation.
    @param lines: Output lines returned by the list command.
    @param operation_id: Synthetic operation identifier.
    @return: The single row beginning with that operation identifier.
    """
    return next(line for line in lines if line.split(" ")[0] == operation_id)


def refusals(lines: list[str]) -> list[str]:
    """@description: Collect refusal lines so tests can assert the refusal class without relying on prose.
    @param lines: Output lines returned by one demo command.
    @return: Lines that begin with the refusal marker.
    """
    return [line for line in lines if line.startswith("拒絕")]


def test_banner_states_that_all_data_is_synthetic_and_no_service_is_contacted():
    """@description: Make the offline boundary visible before the Owner types any command.
    @param: none
    @return: None.
    """
    lines = run_script([])
    assert any("合成資料" in line for line in lines)
    assert any("不連線" in line and "MySQL" in line and "Discord" in line for line in lines)


def test_list_shows_all_five_operations_with_expected_identity_statuses():
    """@description: Present the seeded identity states that the Owner needs before reviewing anything.
    @param: none
    @return: None.
    """
    lines = run_script(["list"])
    expected = {"op-1": "UNRESOLVED", "op-2": "CONFIRMED", "op-3": "CONFLICT", "op-4": "UNRESOLVED", "op-5": "UNRESOLVED"}
    for operation_id, identity_status in expected.items():
        assert identity_status in operation_row(lines, operation_id)


def test_show_reports_conflict_reason_and_every_evidence_record_for_the_conflicted_operation():
    """@description: Expose the audit facts behind a persisted identity conflict.
    @param: none
    @return: None.
    """
    lines = run_script(["show op-3"])
    assert any("CONFLICT" in line for line in lines)
    assert any("衝突" in line for line in lines)
    assert sum("owner_review" in line for line in lines) >= 2


def test_show_keeps_platform_only_evidence_visible_and_identity_unresolved():
    """@description: Show that a title-level platform signal is recorded but does not confirm a version.
    @param: none
    @return: None.
    """
    lines = run_script(["show op-5"])
    assert any("platform_metadata" in line for line in lines)
    assert any("UNRESOLVED" in line for line in lines)
    assert not any("CONFIRMED" in line for line in lines)


def test_non_owner_preview_is_refused_and_prints_no_token():
    """@description: Refuse a preview from a non-Owner identity before any confirmation token is shown.
    @param: none
    @return: None.
    """
    lines = run_script([f"as {STRANGER_ID}", "preview op-1 approve demo-cite-1 synthetic verified song"])
    assert any("PermissionError" in line for line in refusals(lines))
    assert not any("token=" in line for line in lines)


def test_switching_back_to_the_owner_restores_preview_rights():
    """@description: Show that the acting identity is the only input deciding Owner authority.
    @param: none
    @return: None.
    """
    session = new_session()
    session.execute(f"as {STRANGER_ID}")
    assert refusals(session.execute("preview op-1 approve demo-cite-1 synthetic verified song"))
    session.execute(f"as {OWNER_ID}")
    assert token_from(session.execute("preview op-1 approve demo-cite-1 synthetic verified song"))


def test_approve_preview_then_confirm_applies_and_a_repeated_confirm_returns_the_same_output():
    """@description: Execute an approval once and replay the identical result on a second confirm.
    @param: none
    @return: None.
    """
    session = new_session()
    token = token_from(session.execute("preview op-1 approve demo-cite-1 synthetic verified song"))
    first = session.execute(f"confirm {token}")
    second = session.execute(f"confirm {token}")
    assert any("status=approved" in line for line in first)
    assert first == second


def test_expired_token_is_refused_after_tick_past_the_time_limit():
    """@description: Demonstrate that a preview token stops working after its five-minute window.
    @param: none
    @return: None.
    """
    session = new_session()
    token = token_from(session.execute("preview op-1 reject demo-cite-1 not an eligible song"))
    session.execute("tick 301")
    lines = session.execute(f"confirm {token}")
    assert any("ExpiredReviewToken" in line for line in refusals(lines))
    assert any("status=pending_review" in line for line in session.execute("show op-1"))


def test_stale_token_is_refused_after_another_decision_changed_the_revision():
    """@description: Refuse an older approval once a rejection has already advanced the review revision.
    @param: none
    @return: None.
    """
    session = new_session()
    stale_token = token_from(session.execute("preview op-1 approve demo-cite-1 synthetic verified song"))
    fresh_token = token_from(session.execute("preview op-1 reject demo-cite-1 not an eligible song"))
    session.execute(f"confirm {fresh_token}")
    lines = session.execute(f"confirm {stale_token}")
    assert any("StaleReviewVersion" in line for line in refusals(lines))
    assert any("status=rejected" in line for line in session.execute("show op-1"))


def test_fault_next_then_confirm_then_retry_applies_the_approval_exactly_once():
    """@description: Replay an ambiguous commit with the same decision key and keep one audit revision.
    @param: none
    @return: None.
    """
    session = new_session()
    token = token_from(session.execute("preview op-1 approve demo-cite-1 synthetic verified song"))
    history_before = len(session.database.state["jb2_review_history"])
    session.execute("fault next")  # [armed after preview]: A read must not consume the one-shot commit fault.
    ambiguous = session.execute(f"confirm {token}")
    assert any("ReviewOutcomeUnknown" in line for line in refusals(ambiguous))
    retried = session.execute(f"retry {token}")
    assert any("status=approved" in line for line in retried)
    assert len(session.database.state["jb2_review_history"]) == history_before + 1


def test_fault_is_cleared_after_one_use_so_the_next_commit_is_healthy():
    """@description: Keep the injected ambiguity from leaking into later demonstrations.
    @param: none
    @return: None.
    """
    session = new_session()
    token = token_from(session.execute("preview op-1 approve demo-cite-1 synthetic verified song"))
    session.execute("fault next")
    session.execute(f"confirm {token}")
    assert session.database.commit_fault is None


def test_evidence_entry_keeps_status_identity_and_decision_unchanged():
    """@description: Record re-examination evidence as an audit fact while the decision stays where it was.
    @param: none
    @return: None.
    """
    lines = run_script(["evidence op-1 owner - fresh owner inspection"])
    unchanged = [line for line in lines if "未變" in line]
    assert len(unchanged) >= 3
    assert not any("已改變" in line for line in lines)


def test_platform_only_evidence_keeps_operation_five_unresolved():
    """@description: Add a platform-level signal to op-5 and confirm the identity is still UNRESOLVED afterward.
    @param: none
    @return: None.
    """
    session = new_session()
    session.execute("evidence op-5 platform demo-version-A title only")
    row = operation_row(session.execute("list"), "op-5")
    assert "UNRESOLVED" in row
    assert not any("CONFIRMED" in line for line in session.execute("show op-5"))


def test_help_lists_every_required_command_and_unknown_input_is_reported():
    """@description: Make the demo self-explanatory for the Owner without printing review results.
    @param: none
    @return: None.
    """
    lines = run_script(["help", "no-such-command"])
    for keyword in ("list", "show", "as", "preview", "confirm", "retry", "fault", "tick", "evidence", "quit"):
        assert any(line.startswith(keyword) or f" {keyword} " in line for line in lines)
    assert any("未知指令" in line for line in lines)


def test_quit_stops_processing_later_script_lines():
    """@description: End the session immediately so later input is never executed.
    @param: none
    @return: None.
    """
    lines = run_script(["quit", "tick 999"])
    assert not any("時鐘" in line for line in lines)
