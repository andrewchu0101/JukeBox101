"""Verify the Owner reminder: it counts the queue, speaks only to the Owner, never carries a link or official value, and is throttled.

The review service, sender and clock are fakes; no Discord or database is involved.
"""

import asyncio
from types import SimpleNamespace

from bot.exchange_delivery import DmResult
from bot.owner_notifier import NOTICE_INTERVAL_SECONDS, OWNER_LIST_COMMAND, OwnerNotifier, notice_text

OWNER = 777


class FakeService:
    """Answer list_pending from a mutable list of sequences."""

    def __init__(self, sequences, more=False, failure=None) -> None:
        """@description: Store the queue.
        @param self: Fake.
        @param sequences: Queue positions waiting for the Owner.
        @param more: Whether the queue is longer than the page.
        @param failure: Exception to raise instead.
        @return: None.
        """
        self.sequences, self.more, self.failure, self.calls = list(sequences), more, failure, []

    def list_pending(self, actor_id, *, after_sequence=None, limit=20):
        """@description: Return the queue as a page.
        @param self: Fake.
        @param actor_id: Caller.
        @param after_sequence: Cursor.
        @param limit: Page size.
        @return: Page-like object.
        """
        self.calls.append((actor_id, after_sequence, limit))
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(items=tuple(SimpleNamespace(accepted_sequence=value) for value in self.sequences),
                               next_after_sequence=1 if self.more else None)


class FakeSender:
    """Record every direct message and answer with a scripted result."""

    def __init__(self, result=None) -> None:
        """@description: Start empty.
        @param self: Fake.
        @param result: Result to return; delivered when None.
        @return: None.
        """
        self.sent, self.result = [], result or DmResult.delivered(1)

    async def send(self, discord_user_id, text):
        """@description: Record one message.
        @param self: Fake.
        @param discord_user_id: Recipient.
        @param text: Body.
        @return: Scripted result.
        """
        self.sent.append((discord_user_id, text))
        return self.result


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        """@description: Start at 1000 seconds.
        @param self: Fake.
        @return: None.
        """
        self.now = 1000.0

    def __call__(self) -> float:
        """@description: Read the clock.
        @param self: Fake.
        @return: Seconds.
        """
        return self.now


def build(service, sender=None, clock=None):
    """@description: Build a notifier over fakes.
    @param service: Fake service.
    @param sender: Fake sender.
    @param clock: Fake clock.
    @return: Notifier, sender and clock.
    """
    sender, clock = sender or FakeSender(), clock or Clock()
    return OwnerNotifier(lambda: service, sender, OWNER, clock=clock), sender, clock


def run(notifier) -> bool:
    """@description: Run one check.
    @param notifier: Notifier.
    @return: Whether a reminder was delivered.
    """
    return asyncio.run(notifier.notify_if_needed())


def test_an_empty_queue_sends_nothing() -> None:
    """@description: No waiting case means no message.
    @param: none
    @return: None.
    """
    notifier, sender, _ = build(FakeService([]))
    assert run(notifier) is False and sender.sent == []


def test_a_waiting_case_sends_one_reminder_only_to_the_owner_that_names_the_command_and_the_count() -> None:
    """@description: The reminder says how many wait and which command to run.
    @param: none
    @return: None.
    """
    service = FakeService([4, 7])
    notifier, sender, _ = build(service)
    assert run(notifier) is True
    recipient, text = sender.sent[0]
    assert recipient == OWNER and OWNER_LIST_COMMAND in text and "2 筆" in text and service.calls[0][0] == OWNER


def test_the_reminder_carries_no_link_or_official_value() -> None:
    """@description: Nothing from the cases is copied into the message.
    @param: none
    @return: None.
    """
    notifier, sender, _ = build(FakeService([1, 2, 3]))
    run(notifier)
    text = sender.sent[0][1]
    assert "http" not in text and "youtube" not in text.casefold() and "<" not in text


def test_a_long_queue_is_reported_as_more_than_the_counted_slice() -> None:
    """@description: The count never claims more precision than it has.
    @param: none
    @return: None.
    """
    assert "筆以上" in notice_text(20, True) and "筆以上" not in notice_text(3, False)


def test_nothing_new_since_the_last_reminder_sends_nothing_even_after_the_interval() -> None:
    """@description: A reminder is about something new, not a nag.
    @param: none
    @return: None.
    """
    notifier, sender, clock = build(FakeService([5]))
    run(notifier)
    clock.now += NOTICE_INTERVAL_SECONDS * 3
    assert run(notifier) is False and len(sender.sent) == 1


def test_a_new_case_inside_the_interval_waits_and_is_announced_after_it() -> None:
    """@description: A burst of cases becomes one reminder per interval.
    @param: none
    @return: None.
    """
    service = FakeService([5])
    notifier, sender, clock = build(service)
    run(notifier)
    service.sequences.append(9)
    clock.now += 10
    assert run(notifier) is False and len(sender.sent) == 1
    clock.now += NOTICE_INTERVAL_SECONDS
    assert run(notifier) is True and len(sender.sent) == 2


def test_a_closed_direct_message_is_not_retried_every_round_and_is_retried_later() -> None:
    """@description: A failed reminder is throttled too, and the case is still announced once delivery works.
    @param: none
    @return: None.
    """
    sender = FakeSender(DmResult.unknown("forbidden_50007"))
    notifier, _, clock = build(FakeService([3]), sender)
    assert run(notifier) is False and run(notifier) is False and len(sender.sent) == 1
    sender.result = DmResult.delivered(2)
    clock.now += NOTICE_INTERVAL_SECONDS
    assert run(notifier) is True and len(sender.sent) == 2


def test_a_failing_queue_check_never_raises_and_sends_nothing() -> None:
    """@description: A database trouble is logged and the next round tries again.
    @param: none
    @return: None.
    """
    notifier, sender, _ = build(FakeService([1], failure=RuntimeError("down")))
    assert run(notifier) is False and sender.sent == []
