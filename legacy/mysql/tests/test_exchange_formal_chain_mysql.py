"""Verify the labelled synthetic /exchange call chain on the explicitly isolated MySQL target: MusicService.synthetic_exchange, exchange_and_deliver and a real SubmissionRepository.

Only the two external boundaries are replaced: the direct-message adapter (a recorder returning scripted DmResult values) and the ephemeral reply
adapter (a recorder). Nothing else is rewritten; the service, the delivery program and the repository are the production objects.
This is the synthetic acceptance route. It is not the formal /exchange command, which still replies with a fixed "not open" text, and it
does not verify any real Discord direct message.

Database commit unknown and direct-message delivery unknown are different events. Commit-unknown cases live in test_unknown_commit_mysql.py and
are not repeated here; the cases below exercise only the DM outcome that the adapter reports.

Opt-in only: every test is skipped unless JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED=1 and the isolated target is configured.
The fixture is the recorded guarded fixture from test_compensation_mysql.py: the compensation tables must already exist, the switch is off as in production.
"""

import asyncio
import os
import random
from datetime import datetime, timedelta, timezone

import pytest

from bot.database.submission_repository import SubmissionRepository
from bot.exchange_delivery import DeliveryStatus, DmResult, PENDING_REVIEW_TEXT
from bot.services import SYNTHETIC_LABEL, MusicService
from tests.isolated_mysql_target import ISOLATION_SWITCH, open_verified_connection
from tests.test_compensation_mysql import guarded_funded_pair, later, read_row  # noqa: F401  (fixture imported by name)
from tests.test_exchange_mysql_integration import USER_A, fixture_footprint, isolated, verified_transaction  # noqa: F401  (isolated: fixture imported by name)
from tests.test_exchange_repository_mysql import assert_repository_state
from tests.test_identity_service_path import OWNER_ID, submission

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(os.environ.get(ISOLATION_SWITCH) != "1", reason="Requires explicitly authorized isolated MySQL target"),
]

PEER_LINK = submission(2).source_url  # [synthetic link]: The only candidate for the requester, from the fixture's peer.


class RecordingSender:
    """External boundary: the direct-message adapter. Records every attempt and returns scripted outcomes in order; never contacts Discord."""

    def __init__(self, outcomes: list) -> None:
        """@description: Script the outcomes of the next attempts; later attempts report a delivered result.
        @param outcomes: DmResult values returned in order.
        @return: None.
        """
        self._outcomes = list(outcomes)
        self.sent: list = []

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Record one direct-message attempt and return the scripted outcome.
        @param discord_user_id: Recipient.
        @param text: Message body as the production sender built it.
        @return: Scripted DmResult.
        """
        self.sent.append((discord_user_id, text))
        if self._outcomes:
            return self._outcomes.pop(0)
        return DmResult.delivered(900000 + len(self.sent))


class RecordingReplier:
    """External boundary: the requester-only reply adapter. Records every reply; never contacts Discord."""

    def __init__(self) -> None:
        """@description: Start with no replies recorded.
        @return: None.
        """
        self.texts: list = []

    async def send_ephemeral(self, text: str) -> None:
        """@description: Record one reply.
        @param text: Reply body as the production flow built it.
        @return: None.
        """
        self.texts.append(text)


def synthetic(service: MusicService, key: str, sender: RecordingSender, replier: RecordingReplier):
    """@description: Call the existing synthetic /exchange route with the production service, the production delivery program and the given boundary adapters.
    @param service: Production MusicService with the synthetic route enabled.
    @param key: Technical request identity, as the handler builds it.
    @param sender: Recording direct-message adapter.
    @param replier: Recording reply adapter.
    @return: Flow result returned by the service.
    """
    return asyncio.run(service.synthetic_exchange(USER_A, key, sender, replier))


def repository_with_switch_on(target) -> SubmissionRepository:
    """@description: A switched-on repository over the same isolated target, used only to set up a stranded state that the switched-off route must still honour.
    @param target: Verified isolated destination.
    @return: Switched-on repository.
    """
    settings, pin = target
    return SubmissionRepository(lambda: open_verified_connection(settings, pin), OWNER_ID, compensation_enabled=True)


def backdate(target, recommendation_id: int) -> None:
    """@description: Move one fixture-owned reservation to an hour ago so it counts as stranded at the real clock; the row is identified by its id and the fixture cleanup removes it.
    @param target: Verified isolated destination.
    @param recommendation_id: Reservation created by this test.
    @return: None.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE recommendations SET created_at = %s WHERE recommendation_id = %s",
                           (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1), recommendation_id))
            assert cursor.rowcount == 1


@pytest.fixture
def chain(guarded_funded_pair):
    """@description: The production synthetic route over the recorded guarded world, with the requester funded and one peer candidate.
    @param guarded_funded_pair: Recorded guarded fixture (switch off, three tables present, funded pair).
    @return: Tuple of verified target, production repository and synthetic MusicService.
    """
    target, repository, _ = guarded_funded_pair
    return target, repository, MusicService(None, submission_repository=repository, synthetic_exchange=True)


def test_c1_a_normal_synthetic_exchange_debits_once_and_sends_one_labelled_link_message(chain):
    """@description: A normal request debits one point, sends one direct message carrying the verified link, and replies with the synthetic label and no link.
    @param chain: Production synthetic route over the recorded guarded world.
    @return: None.
    """
    target, _, service = chain
    sender, replier = RecordingSender([]), RecordingReplier()
    flow = synthetic(service, "c1-key", sender, replier)
    assert flow.delivery_status is DeliveryStatus.DELIVERED and flow.recommendation_id is not None
    assert len(sender.sent) == 1
    assert PEER_LINK in sender.sent[0][1] and SYNTHETIC_LABEL in sender.sent[0][1]
    assert flow.reply_text.startswith(SYNTHETIC_LABEL) and PEER_LINK not in flow.reply_text
    assert replier.texts and all(PEER_LINK not in text for text in replier.texts)
    assert read_row(target, "SELECT status FROM recommendations WHERE recommendation_id=%s", (flow.recommendation_id,))["status"] == "delivered"
    assert_repository_state((target, None, None), 1, 0)


def test_c2_switched_off_with_the_tables_present_an_open_incident_blocks_and_sends_nothing(chain):
    """@description: With the switch off and the tables present, an open incident blocks the requester's new synthetic request: the reply says so, nothing is debited and no direct message is sent.
    @param chain: Production synthetic route over the recorded guarded world.
    @return: None.
    """
    target, _, service = chain
    setup = repository_with_switch_on(target)
    reservation = setup.exchange(USER_A, "c2-original", random.Random(1), now=later()).recommendation_id
    setup.owner_approve_incident(OWNER_ID, reservation, "c2", later())
    backdate(target, reservation)
    before = fixture_footprint(target)
    sender, replier = RecordingSender([]), RecordingReplier()
    flow = synthetic(service, "c2-new", sender, replier)
    assert flow.delivery_status is None and flow.recommendation_id is None
    assert flow.reply_text.startswith(SYNTHETIC_LABEL) and PENDING_REVIEW_TEXT in flow.reply_text
    assert sender.sent == []
    assert fixture_footprint(target) == before
    assert_repository_state((target, None, None), 1, 0)


def test_c3_the_same_request_key_replays_without_a_second_debit_or_a_second_message(chain):
    """@description: Repeating the same synthetic request key returns the original reservation: no second debit and no second direct message.
    @param chain: Production synthetic route over the recorded guarded world.
    @return: None.
    """
    target, _, service = chain
    first_sender, first_replier = RecordingSender([]), RecordingReplier()
    first = synthetic(service, "c3-key", first_sender, first_replier)
    replay_sender, replay_replier = RecordingSender([]), RecordingReplier()
    replay = synthetic(service, "c3-key", replay_sender, replay_replier)
    assert first.delivery_status is DeliveryStatus.DELIVERED and replay.delivery_status is DeliveryStatus.DELIVERED
    assert replay.recommendation_id == first.recommendation_id
    assert len(first_sender.sent) == 1 and replay_sender.sent == []
    assert_repository_state((target, None, None), 1, 0)


def test_c4_an_explicit_not_sent_refunds_once_releases_the_song_and_a_replay_refunds_nothing_more(chain):
    """@description: An explicit not-sent outcome refunds the one point once and releases the reservation; a replay of the same key refunds nothing again; the released song can be reserved again.
    @param chain: Production synthetic route over the recorded guarded world.
    @return: None.
    """
    target, _, service = chain
    failed_sender = RecordingSender([DmResult.not_sent("synthetic_refusal")])
    failed = synthetic(service, "c4-key", failed_sender, RecordingReplier())
    assert failed.delivery_status is DeliveryStatus.FAILED and failed.refund_status == "refunded"
    assert len(failed_sender.sent) == 1
    assert read_row(target, "SELECT status, released_at FROM recommendations WHERE recommendation_id=%s",
                    (failed.recommendation_id,))["released_at"] is not None
    assert read_row(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='delivery_refund'", ())["total"] == 1
    replay_sender = RecordingSender([])
    replay = synthetic(service, "c4-key", replay_sender, RecordingReplier())
    assert replay.recommendation_id == failed.recommendation_id and replay_sender.sent == []
    assert read_row(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='delivery_refund'", ())["total"] == 1
    assert_repository_state((target, None, None), 1, 1)
    again_sender = RecordingSender([])
    again = synthetic(service, "c4-new", again_sender, RecordingReplier())
    assert again.delivery_status is DeliveryStatus.DELIVERED and len(again_sender.sent) == 1
    assert_repository_state((target, None, None), 2, 0)


def test_c5_an_unknown_direct_message_outcome_is_never_refunded_or_resent_and_keeps_the_song(chain):
    """@description: An unknown direct-message outcome is recorded as uncertain: the point is not refunded, the song stays held, and a replay of the same key sends nothing.
    @param chain: Production synthetic route over the recorded guarded world.
    @return: None.
    """
    target, _, service = chain
    unknown_sender = RecordingSender([DmResult.unknown("synthetic_timeout")])
    unknown = synthetic(service, "c5-key", unknown_sender, RecordingReplier())
    assert unknown.delivery_status is DeliveryStatus.UNCERTAIN and unknown.refund_status is None
    assert len(unknown_sender.sent) == 1
    row = read_row(target, "SELECT status, released_at FROM recommendations WHERE recommendation_id=%s", (unknown.recommendation_id,))
    assert row["status"] == "uncertain" and row["released_at"] is None
    assert read_row(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='delivery_refund'", ())["total"] == 0
    replay_sender = RecordingSender([])
    replay = synthetic(service, "c5-key", replay_sender, RecordingReplier())
    assert replay.delivery_status is DeliveryStatus.UNCERTAIN and replay.refund_status is None
    assert replay_sender.sent == []
    assert_repository_state((target, None, None), 1, 0)
