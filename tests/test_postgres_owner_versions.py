"""Real PostgreSQL behaviour of the known-version list and the card's version buttons over the real review service.

Only the Discord interaction is simulated; versions, evidence, revisions, settlement and money are the production tables.
"""

import asyncio

import pytest

from bot.database import review_persistence
from bot.discord_owner_cards import OwnerCards
from tests.postgres_support import admin_query
from tests.test_discord_owner_cards import GUILD_ID, Press
from tests.test_owner_e2e_acceptance import Clock
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID, VIDEO_A, VIDEO_B, VIDEO_C, World

pytestmark = pytest.mark.postgres
CAROL, DAVE = 500003, 500004
VIDEO_D = "ddddddddddd"
VERSION_PATH = "'{value,fields,version,fields,value}'"
STATUS_PATH = "'{value,fields,status,value}'"


def cards_of(world: World) -> OwnerCards:
    """@description: Build a card handler over the world's real review service.
    @param world: Automatic-mode database.
    @return: Card handler.
    """
    return OwnerCards(lambda: world.owner, OWNER_ID, GUILD_ID, Clock())


def pending(world: World) -> list:
    """@description: Read the Owner queue exactly as the card does.
    @param world: Automatic-mode database.
    @return: Pending rows in acceptance order.
    """
    return list(world.owner.list_pending(OWNER_ID, after_sequence=None, limit=20).items)


def press(cards: OwnerCards, action: str, item, extra: int = 0, interaction: Press | None = None) -> Press:
    """@description: Press a card button for a row and wait for it to finish.
    @param cards: Card handler.
    @param action: Button action.
    @param item: Pending row the card displayed.
    @param extra: Opaque choice of a version button.
    @param interaction: Press to use, or a default Owner press.
    @return: The press.
    """
    interaction = interaction or Press(OWNER_ID)
    asyncio.run(cards.button_class(action, item.accepted_sequence, item.revision, extra=extra).callback(interaction))
    return interaction


def identities(world: World) -> dict:
    """@description: Read each video's stored version status and version value.
    @param world: Automatic-mode database.
    @return: Video id to (status, version value).
    """
    rows = admin_query(world.database, f"SELECT video_id, identity_json::jsonb #>> {STATUS_PATH} AS status, "
                                       f"identity_json::jsonb #>> {VERSION_PATH} AS version FROM jb2_videos")
    return {row["video_id"]: (row["status"], row["version"]) for row in rows}


def version_rows(world: World) -> int:
    """@description: Count stored versions, which only an explicit Owner action may create.
    @param world: Automatic-mode database.
    @return: Row count.
    """
    return admin_query(world.database, "SELECT count(*) AS n FROM jb2_versions")[0]["n"]


def test_known_versions_are_listed_once_per_recording_ordered_and_bounded(pg_database, monkeypatch):
    """@description: Two videos of one confirmed version give one entry named by its first video; unresolved videos are not listed; a page transfers one row more than it shows.
    @param pg_database: Disposable database.
    @param monkeypatch: Spy on the version query.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-1")
    world.share(BOB, VIDEO_B, "discord:2")
    world.confirm_version("discord:2", "rec-2")
    world.share(CAROL, VIDEO_C, "discord:3")
    world.share(DAVE, VIDEO_D, "discord:4")
    world.confirm_version("discord:4", "rec-1")
    transferred = []
    original = review_persistence.fetch_all

    def spy(cursor, sql, parameters=()):
        """@description: Record how many rows the version query hands back.
        @param cursor: Transaction cursor.
        @param sql: Statement.
        @param parameters: Bound values.
        @return: The rows, unchanged.
        """
        rows = original(cursor, sql, parameters)
        if "GROUP BY" in sql and "jb2_versions" in sql:
            transferred.append(len(rows))
        return rows

    monkeypatch.setattr(review_persistence, "fetch_all", spy)
    first = world.owner.list_known_versions(OWNER_ID, limit=1)
    second = world.owner.list_known_versions(OWNER_ID, after_sequence=first.next_after_sequence, limit=1)
    assert [(item.version_id, item.first_video_id) for item in (*first.items, *second.items)] == [("rec-1", VIDEO_A), ("rec-2", VIDEO_B)]
    assert first.next_after_sequence == first.items[0].first_sequence and second.next_after_sequence is None
    assert max(transferred) <= 2


def test_a_missing_mapping_creates_no_version_until_the_owner_presses(pg_database):
    """@description: Opening the card for a video the system has no mapping for creates no version and leaves it unresolved.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    asyncio.run(cards_of(world).first_replies(OWNER_ID))
    assert version_rows(world) == 0 and identities(world)[VIDEO_A] == ("unresolved", None)


def test_the_new_version_button_creates_an_opaque_version_and_settles_by_the_existing_rules(pg_database):
    """@description: One press records a random version for the video and, with settlement on and every condition met, pays exactly one Nickel and says so.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    shown = press(cards_of(world), "new", pending(world)[0])
    status, version = identities(world)[VIDEO_A]
    assert status == "confirmed" and version.startswith("rv-") and VIDEO_A.lower() not in version.lower()
    world.assert_books_balance(1)
    assert "已為投稿者發放 +1" in shown.shown()
    assert pending(world) == []


def test_choosing_a_known_version_maps_the_video_and_pays_nothing_again(pg_database):
    """@description: Selecting the first video's version makes the second video the same recording: no second version, no second award, no remaining question.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    cards = cards_of(world)
    press(cards, "new", pending(world)[0])
    world.share(BOB, VIDEO_B, "discord:2")
    known = world.owner.list_known_versions(OWNER_ID, limit=5).items[0]
    shown = press(cards, "same", pending(world)[0], extra=known.first_sequence)
    assert identities(world)[VIDEO_B][1] == identities(world)[VIDEO_A][1] and version_rows(world) == 1
    world.assert_books_balance(1)
    assert world.balances().get(BOB, 0) == 0 and pending(world) == []
    assert "已確認" in shown.shown()


def test_two_handlers_pressing_new_at_once_create_one_version_and_one_award(pg_database):
    """@description: Without any shared lock, a simultaneous double press still yields one version and one Nickel because the database decides.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    item = pending(world)[0]
    handlers, presses = [cards_of(world), cards_of(world)], [Press(OWNER_ID), Press(OWNER_ID)]

    async def both():
        """@description: Press the same button through two independent handlers.
        @param: none
        @return: None.
        """
        await asyncio.gather(*(handler.button_class("new", item.accepted_sequence, item.revision).callback(shown)
                               for handler, shown in zip(handlers, presses)))

    asyncio.run(both())
    assert version_rows(world) == 1
    world.assert_books_balance(1)


def test_a_wrong_choice_and_a_stale_card_write_nothing(pg_database):
    """@description: A version that does not exist, and a card whose version the Owner resolved elsewhere, change nothing.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    item = pending(world)[0]
    cards = cards_of(world)
    wrong = press(cards, "same", item, extra=999)
    assert version_rows(world) == 0 and "重新載入" in wrong.shown()
    world.confirm_version("discord:1", "recording-elsewhere")
    stale = press(cards, "new", item)
    assert version_rows(world) == 1 and identities(world)[VIDEO_A][1] == "recording-elsewhere" and "重新載入" in stale.shown()
    world.pipeline.settle_ready()  # [existing settlement]: Pays the version the Owner confirmed elsewhere, once.
    world.assert_books_balance(1)


def test_a_foreign_press_on_a_version_button_writes_nothing(pg_database):
    """@description: A press from another user is refused before any read or write.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    foreign = press(cards_of(world), "new", pending(world)[0], interaction=Press(OWNER_ID + 1))
    assert version_rows(world) == 0 and foreign.edits == [] and foreign.response.messages


def test_a_version_button_on_a_case_still_queued_but_already_mapped_writes_nothing(pg_database):
    """@description: A song still waiting for approval stays in the queue after the Owner mapped its version elsewhere; the old version button must not overwrite that mapping.
    @param pg_database: Disposable database.
    @return: None.
    """
    from tests.test_postgres_auto_pipeline import found
    world = World(pg_database)
    world.metadata.script[VIDEO_A] = found(VIDEO_A, channel="UC" + "b" * 22)  # [untrusted channel]: Duration recorded, approval waits for the Owner.
    world.share(ALICE, VIDEO_A, "discord:1")
    item = pending(world)[0]
    world.confirm_version("discord:1", "recording-elsewhere")
    assert [row.operation_id for row in pending(world)] == ["discord:1"]
    stale = press(cards_of(world), "new", item)
    assert version_rows(world) == 1 and identities(world)[VIDEO_A][1] == "recording-elsewhere" and "重新載入" in stale.shown()
