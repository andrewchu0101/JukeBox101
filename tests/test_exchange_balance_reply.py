"""Verify the /exchange reply tells the requester their remaining Nickel, shows the operation code only when something went wrong, and never claims an uncommitted balance.

Uses the same offline fake database and scripted DM adapter as the delivery tests; no Discord or network is involved.
"""

import pytest

from bot.exchange_delivery import DmResult
from tests.test_exchange_delivery import ScriptedDm, balance, start, two_submitters, world  # noqa: F401  (pytest fixtures imported by name)

CODE_HINT = "提供給 Owner"


def test_a_delivered_exchange_shows_the_remaining_balance_and_no_operation_code(two_submitters) -> None:
    """@description: Success carries no technical code, only the balance that the committed debit left.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    flow = start(repository, ScriptedDm(DmResult.delivered(7001)), 101, "bal-ok")
    assert "操作" not in flow.reply_text and CODE_HINT not in flow.reply_text
    assert f"目前可用 Nickel：{balance(database, 101)}" in flow.reply_text


def test_a_refunded_failure_shows_the_code_the_owner_hint_and_the_refunded_balance(two_submitters) -> None:
    """@description: A proven non-delivery shows the code for the Owner and the balance after the refund committed.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    flow = start(repository, ScriptedDm(DmResult.not_sent("dm_closed")), 101, "bal-fail")
    assert f"操作 {flow.recommendation_id}" in flow.reply_text and CODE_HINT in flow.reply_text
    assert balance(database, 101) == 1 and "目前可用 Nickel：1" in flow.reply_text


def test_an_uncertain_outcome_shows_the_code_the_hint_and_the_balance_after_the_debit(two_submitters) -> None:
    """@description: An unknown outcome keeps the debit, so the balance shown is the post-debit number, with the code for the Owner.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    flow = start(repository, ScriptedDm(DmResult.unknown("timeout")), 101, "bal-unknown")
    assert f"操作 {flow.recommendation_id}" in flow.reply_text and CODE_HINT in flow.reply_text
    assert balance(database, 101) == 0 and "目前可用 Nickel：0" in flow.reply_text


class BrokenBalance:
    """@description: Wrap a repository so only the balance read fails, proving a failed read never changes or hides the exchange result.
    @param inner: Real repository over the fake.
    @return: Repository whose user_exchange_balance raises.
    """

    def __init__(self, inner) -> None:
        """@description: Keep the wrapped repository.
        @param self: Wrapper instance.
        @param inner: Real repository.
        @return: None.
        """
        self.inner = inner

    def __getattr__(self, name: str):
        """@description: Delegate every other attribute to the real repository.
        @param self: Wrapper instance.
        @param name: Attribute requested.
        @return: The wrapped attribute.
        """
        return getattr(self.inner, name)

    def user_exchange_balance(self, discord_user_id: int):
        """@description: Fail like a database outage after the exchange committed.
        @param self: Wrapper instance.
        @param discord_user_id: Requester.
        @return: Never returns.
        """
        raise RuntimeError("balance read failed")


def test_a_failed_balance_read_keeps_the_result_and_omits_only_the_balance(two_submitters) -> None:
    """@description: The reply never invents a balance when it cannot be read, and the delivery result stays correct.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    _, repository, _ = two_submitters
    flow = start(BrokenBalance(repository), ScriptedDm(DmResult.delivered(7002)), 101, "bal-broken")
    assert "已將推薦私訊給你" in flow.reply_text and "Nickel：" not in flow.reply_text


def test_an_insufficient_credit_reply_also_shows_the_balance(two_submitters) -> None:
    """@description: A refusal for lack of Nickel still says how many remain.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    start(repository, ScriptedDm(DmResult.delivered(7003)), 101, "bal-first")
    start(repository, ScriptedDm(DmResult.delivered(7004)), 102, "bal-second")
    flow = start(repository, ScriptedDm(), 101, "bal-none")
    assert f"目前可用 Nickel：{balance(database, 101)}" in flow.reply_text


@pytest.mark.parametrize("outcome", [DmResult.delivered(7005), DmResult.not_sent("dm_closed"), DmResult.unknown("timeout")])
def test_the_balance_line_never_contains_a_link(two_submitters, outcome) -> None:
    """@description: Adding the balance must not weaken the link-free rule of the ephemeral reply.
    @param two_submitters: Two settled submitters with verified songs.
    @param outcome: Scripted DM outcome.
    @return: None.
    """
    _, repository, _ = two_submitters
    assert "youtube.com" not in start(repository, ScriptedDm(outcome), 101, "bal-link").reply_text
