"""Real PostgreSQL behaviour of the Owner case cards over the real review service, repository and app role.

Only the Discord interaction is simulated; revisions, tokens, decisions and money are the production tables.
"""

import asyncio
from functools import partial

import pytest

from bot.database.connection import open_connection
from bot.database.submission_repository import SubmissionRepository
from bot.discord_owner_cards import OwnerCards
from bot.owner_review import OwnerReviewService
from tests.postgres_support import admin_query
from tests.test_discord_owner_cards import GUILD_ID, OTHER_USER_ID, Press
from tests.test_owner_e2e_acceptance import OWNER_ID, TOKEN_TTL, Clock, seed

pytestmark = pytest.mark.postgres


@pytest.fixture
def card_world(pg_database):
    """@description: Build the real repository, review service and card handler over the PostgreSQL app role.
    @param pg_database: Disposable database.
    @return: Tuple of database, repository, service and cards.
    """
    clock = Clock()
    repository = SubmissionRepository(partial(open_connection, pg_database.app), OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, clock, token_ttl=TOKEN_TTL)
    return pg_database, repository, service, OwnerCards(lambda: service, OWNER_ID, GUILD_ID, clock)


def first_card(service: OwnerReviewService):
    """@description: Read the oldest pending row exactly as the card does.
    @param service: Real review service.
    @return: Pending row.
    """
    return service.list_pending(OWNER_ID, after_sequence=None, limit=1).items[0]


def press(cards: OwnerCards, action: str, item, interaction: Press | None = None) -> Press:
    """@description: Press a card button for a row and wait for it to finish.
    @param cards: Card handler.
    @param action: Button action.
    @param item: Pending row the card displayed.
    @param interaction: Press to use, or a default Owner press.
    @return: The press.
    """
    interaction = interaction or Press()
    asyncio.run(cards.button_class(action, item.accepted_sequence, item.revision).callback(interaction))
    return interaction


def facts(database) -> dict:
    """@description: Count the review decisions and the money rows, which a card must change only through the service.
    @param database: Disposable database.
    @return: Review statuses, history rows and ledger rows.
    """
    return {"status": [row["review_status"] for row in admin_query(database, "SELECT review_status FROM jb2_videos ORDER BY video_id")],
            "history": admin_query(database, "SELECT count(*) AS n FROM jb2_review_history")[0]["n"],
            "ledger": admin_query(database, "SELECT count(*) AS n FROM credit_ledger")[0]["n"]}


def test_a_card_approval_records_the_song_decision_and_moves_no_money(card_world):
    """@description: One press approves the displayed revision through the real service; the version is still unknown, so nothing is paid.
    @param card_world: Real repository, service and card handler.
    @return: None.
    """
    database, repository, service, cards = card_world
    seed(repository, 1)
    item = first_card(service)
    before = facts(database)
    shown = press(cards, "approve", item)
    after = facts(database)
    assert after["status"] == ["approved"] and after["history"] == before["history"] + 1 and after["ledger"] == 0
    assert "已確認" in shown.shown() and item.operation_id not in shown.shown()


def test_two_simultaneous_card_presses_record_one_decision(card_world):
    """@description: A double click writes a single approval and the second press is refused as stale, so nothing is decided or paid twice.
    @param card_world: Real repository, service and card handler.
    @return: None.
    """
    database, repository, service, cards = card_world
    seed(repository, 1)
    item = first_card(service)
    before = facts(database)
    first, second = Press(), Press()

    async def both():
        """@description: Press the same button twice at the same moment.
        @param: none
        @return: None.
        """
        button = cards.button_class("approve", item.accepted_sequence, item.revision)
        await asyncio.gather(button.callback(first), button.callback(second))

    asyncio.run(both())
    assert facts(database)["history"] == before["history"] + 1 and facts(database)["ledger"] == 0
    assert sorted("已確認" in press_.shown() for press_ in (first, second)) == [False, True]


def test_an_old_card_is_refused_after_the_case_changed_and_a_restart_does_not_revive_it(card_world):
    """@description: After another decision, the old button writes nothing, including through a freshly built handler as after a restart.
    @param card_world: Real repository, service and card handler.
    @return: None.
    """
    database, repository, service, cards = card_world
    seed(repository, 1)
    item = first_card(service)
    modal = press(cards, "reject", item).response.modals[0]
    modal.reason._value = "不是歌曲"
    asyncio.run(modal.on_submit(Press()))
    decided = facts(database)
    restarted = OwnerCards(lambda: service, OWNER_ID, GUILD_ID, Clock())
    stale = press(restarted, "approve", item)
    assert facts(database) == decided and "重新載入" in stale.shown()


def test_a_non_owner_press_writes_nothing(card_world):
    """@description: A press from another user is refused before any read or write.
    @param card_world: Real repository, service and card handler.
    @return: None.
    """
    database, repository, service, cards = card_world
    seed(repository, 1)
    item = first_card(service)
    before = facts(database)
    foreign = press(cards, "approve", item, Press(OTHER_USER_ID))
    assert facts(database) == before and foreign.response.messages and foreign.edits == []


def test_next_shows_the_following_case_without_changing_anything(card_world):
    """@description: Next only moves the display: the queue, the order and the stored decisions stay exactly as they were.
    @param card_world: Real repository, service and card handler.
    @return: None.
    """
    database, repository, service, cards = card_world
    seed(repository, 1)
    seed(repository, 2)
    item = first_card(service)
    before = facts(database)
    shown = press(cards, "next", item)
    assert facts(database) == before and [c.custom_id for c in shown.edits[-1]["view"].children][0].startswith("ocard:approve:2:")
    assert [row.operation_id for row in service.list_pending(OWNER_ID).items] == ["op-1", "op-2"]


@pytest.fixture
def paying_world(pg_database):
    """@description: Build the automatic-mode world with one untrusted-channel case whose version the Owner already confirmed, so a card approval pays exactly one Nickel.
    @param pg_database: Disposable database.
    @return: The world and the pending row shown on the card.
    """
    from tests.test_postgres_auto_pipeline import ALICE, OWNER_ID as WORLD_OWNER, VIDEO_B, World, found
    world = World(pg_database)
    world.metadata.script[VIDEO_B] = found(VIDEO_B, channel="UC" + "b" * 22)  # [untrusted channel]: Duration is recorded, approval waits for the Owner.
    world.share(ALICE, VIDEO_B, "discord:b")
    world.confirm_version("discord:b", "recording-b")
    return world, world.owner.list_pending(WORLD_OWNER, after_sequence=None, limit=1).items[0]


def test_two_handlers_without_a_shared_lock_pay_one_nickel_once(paying_world):
    """@description: The in-memory lock is only a courtesy: two handlers that share nothing still produce one decision and one award, because the database transaction decides.
    @param paying_world: Automatic-mode world and the card row.
    @return: None.
    """
    from tests.test_postgres_auto_pipeline import OWNER_ID as WORLD_OWNER
    world, item = paying_world
    clock = Clock()
    handlers = [OwnerCards(lambda: world.owner, WORLD_OWNER, GUILD_ID, clock) for _ in range(2)]
    presses = [Press(WORLD_OWNER), Press(WORLD_OWNER)]

    async def both():
        """@description: Press the same approval through two independent handlers at the same moment.
        @param: none
        @return: None.
        """
        await asyncio.gather(*(handler.button_class("approve", item.accepted_sequence, item.revision).callback(shown)
                               for handler, shown in zip(handlers, presses)))

    asyncio.run(both())
    world.assert_books_balance(1)
    assert sorted("已確認" in shown.shown() for shown in presses) == [False, True]


def test_after_a_restart_the_old_approval_button_cannot_pay_again(paying_world):
    """@description: A fresh process has a new service, token store and handler; the old button still meets the advanced revision and writes nothing.
    @param paying_world: Automatic-mode world and the card row.
    @return: None.
    """
    from tests.test_postgres_auto_pipeline import OWNER_ID as WORLD_OWNER
    world, item = paying_world
    first = Press(WORLD_OWNER)
    asyncio.run(OwnerCards(lambda: world.owner, WORLD_OWNER, GUILD_ID, Clock()).button_class("approve", item.accepted_sequence, item.revision).callback(first))
    world.assert_books_balance(1)
    restarted_service = OwnerReviewService(world.repository, WORLD_OWNER, Clock(), token_ttl=TOKEN_TTL,
                                           settlement_followup=world.new_pipeline().owner_settlement_note)
    again = Press(WORLD_OWNER)
    asyncio.run(OwnerCards(lambda: restarted_service, WORLD_OWNER, GUILD_ID, Clock()).button_class("approve", item.accepted_sequence, item.revision).callback(again))
    world.assert_books_balance(1)
    assert "重新載入" in again.shown()
