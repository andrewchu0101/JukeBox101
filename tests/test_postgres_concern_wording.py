"""Real PostgreSQL behaviour of the wording shown for a case the automatic comparison sent to the Owner.

Spec (wording constants are named so the Owner can change the text without touching the tests):
- bot.owner_decisions.NEAR_DUPLICATE_ADVICE = "高度相同，建議回答「不是」" is added to the doubt line only when all three conditions hold against the SAME
  existing song: same channel, duration difference of at most 3 seconds, and a highly similar title. It is advice for the Owner, not a state: the case
  stays an ordinary pending review.
- bot.owner_decisions.CONCERN_TEXT maps every ConcernCode to one plain sentence; the doubt line of case_choices lists one sentence per concern, naming the
  existing song by the acceptance order the shell looked up (or a generic phrase without one). Titles, channel IDs and durations of other songs are never shown or stored.
- bot.auto_pipeline.SIMILAR_REVIEW_REPLY = "已轉交審核，高度相似，尚未發放" is the submitter's reply when a similar-title concern sent the case to review;
  other concerns keep the generic "待 Owner 審核" reply. Neither reply claims a Nickel, names the other song or names its submitter.
"""

import pytest

from bot.auto_pipeline import SIMILAR_REVIEW_REPLY, format_share_outcome
from bot.owner_decisions import CONCERN_TEXT, NEAR_DUPLICATE_ADVICE, UNKNOWN_SONG_TEXT, case_choices
from bot.review import ReviewStatus
from bot.version_concerns import ConcernCode
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID, TRUSTED, VIDEO_A, VIDEO_B, VIDEO_C
from tests.test_postgres_auto_version_confirm import TITLE_CANARY, ConfirmWorld, official

pytestmark = pytest.mark.postgres

OTHER_CHANNEL = "UC" + "y" * 22  # [second channel]: A different channel that never matches the candidate's.
FIRST_KNOWN_ORDER = 7  # [looked-up order]: The acceptance order the shell would pass for the first existing song; the next song gets the next number.


def said(code: ConcernCode, order: int | None = None) -> str:
    """@description: Build the sentence the doubt line must contain for one concern.
    @param code: Concern code.
    @param order: Acceptance order of the existing song the shell looked up, or None for the generic phrase.
    @return: The sentence.
    """
    return CONCERN_TEXT[code].format(song=UNKNOWN_SONG_TEXT if order is None else f"#{order}")


@pytest.fixture
def world(pg_database) -> ConfirmWorld:
    """@description: Provide one comparing world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return ConfirmWorld(pg_database)


def flag(world: ConfirmWorld, candidate, *old) -> tuple:
    """@description: Submit the old songs first, then the candidate, and return the candidate's reply and the doubt line the core writes for it.
    @param world: Comparing world.
    @param candidate: Metadata of the candidate (submitted last by BOB).
    @param old: Metadata of the existing songs, submitted first by ALICE.
    @return: Reply text and the doubt line.
    """
    for index, metadata in enumerate(old, start=1):
        world.metadata.scripted[metadata.video_id] = metadata
        world.share(ALICE, metadata.video_id, f"discord:{index}")
    world.metadata.scripted[candidate.video_id] = candidate
    operation = f"discord:{len(old) + 1}"
    outcome = world.share(BOB, candidate.video_id, operation)
    item = next(row for row in world.owner.list_pending(OWNER_ID, after_sequence=None, limit=20).items if row.submitter_id == BOB)
    orders = {metadata.video_id: FIRST_KNOWN_ORDER + index for index, metadata in enumerate(old)}
    return format_share_outcome(operation, outcome), case_choices(item, orders).doubt_text


def test_the_wording_constants_are_the_texts_the_owner_was_shown() -> None:
    """@description: Lock the proposed texts; a wording change is then a visible, reviewed edit of one constant.
    @param: none
    @return: None.
    """
    assert NEAR_DUPLICATE_ADVICE == "高度相同，建議回答「不是」"
    assert SIMILAR_REVIEW_REPLY == "已轉交審核，高度相似，尚未發放"


def test_every_concern_code_in_use_has_a_plain_language_sentence() -> None:
    """@description: A concern with no sentence would show the Owner a bare code.
    @param: none
    @return: None.
    """
    used = {ConcernCode.SAME_CHANNEL_CLOSE_DURATION, ConcernCode.SIMILAR_TITLE, ConcernCode.VERSION_KEYWORD, ConcernCode.TITLE_UNJUDGEABLE}
    assert used <= set(CONCERN_TEXT) and all(CONCERN_TEXT[code].strip() for code in used)
    assert len({CONCERN_TEXT[code] for code in used}) == len(used)


def test_all_three_conditions_against_one_song_show_the_advice_the_sentences_and_the_song_number(world) -> None:
    """@description: Same channel, one second apart and a near-identical title: the doubt line says so, names the existing song by number and advises answering no.
    @param world: Comparing world.
    @return: None.
    """
    old = official(VIDEO_A, f"{TITLE_CANARY} Alpha Beta Gamma", 189)
    reply, card = flag(world, official(VIDEO_B, f"{TITLE_CANARY} alpha beta gamma (Official Video)", 190), old)
    assert NEAR_DUPLICATE_ADVICE in card
    assert said(ConcernCode.SAME_CHANNEL_CLOSE_DURATION, FIRST_KNOWN_ORDER) in card and said(ConcernCode.SIMILAR_TITLE, FIRST_KNOWN_ORDER) in card
    assert SIMILAR_REVIEW_REPLY in reply and "+1" not in reply
    assert TITLE_CANARY not in card + reply and str(ALICE) not in reply and VIDEO_A not in reply


def test_the_case_stays_an_ordinary_pending_review_with_no_new_state(world) -> None:
    """@description: The note is advice; the Owner can still approve and confirm, and nothing else changes.
    @param world: Comparing world.
    @return: None.
    """
    flag(world, official(VIDEO_B, "alpha beta gamma (Official Video)", 190), official(VIDEO_A, "Alpha Beta Gamma", 189))
    review = world.owner.view(OWNER_ID, "discord:2").review
    assert review.status is ReviewStatus.PENDING_REVIEW and world.balances().get(BOB, 0) == 0


def test_a_similar_title_alone_shows_its_sentence_and_song_number_but_not_the_advice(world) -> None:
    """@description: Different channel and a clearly different duration: only the title concern holds.
    @param world: Comparing world.
    @return: None.
    """
    old = official(VIDEO_A, "Alpha Beta Gamma", 300, OTHER_CHANNEL)
    reply, card = flag(world, official(VIDEO_B, "ALPHA BETA GAMMA", 189), old)
    assert said(ConcernCode.SIMILAR_TITLE, FIRST_KNOWN_ORDER) in card and NEAR_DUPLICATE_ADVICE not in card
    assert SIMILAR_REVIEW_REPLY in reply


def test_a_close_duration_on_one_channel_alone_shows_its_sentence_but_not_the_advice_or_the_similar_reply(world) -> None:
    """@description: Same channel and one second apart but unrelated titles: the submitter is not told the song is similar.
    @param world: Comparing world.
    @return: None.
    """
    reply, card = flag(world, official(VIDEO_B, "Alpha Beta Gamma", 190), official(VIDEO_A, "Beta Gamma Delta", 189))
    assert said(ConcernCode.SAME_CHANNEL_CLOSE_DURATION, FIRST_KNOWN_ORDER) in card and NEAR_DUPLICATE_ADVICE not in card
    assert SIMILAR_REVIEW_REPLY not in reply and "+1" not in reply


def test_a_version_keyword_alone_shows_its_sentence_without_a_song_number_or_the_advice(world) -> None:
    """@description: The keyword concern names no existing song.
    @param world: Comparing world.
    @return: None.
    """
    reply, card = flag(world, official(VIDEO_B, "Alpha Beta Gamma (Live)", 400), official(VIDEO_A, "Zzzz Qqqq 1234", 189))
    assert CONCERN_TEXT[ConcernCode.VERSION_KEYWORD] in card and NEAR_DUPLICATE_ADVICE not in card and f"#{FIRST_KNOWN_ORDER}" not in card
    assert SIMILAR_REVIEW_REPLY not in reply


def test_conditions_spread_over_two_different_songs_do_not_show_the_advice(world) -> None:
    """@description: A similar title to one song and a close duration to another is not "highly identical" to any single song.
    @param world: Comparing world.
    @return: None.
    """
    similar_elsewhere = official(VIDEO_A, "Alpha Beta Gamma", 300, OTHER_CHANNEL)
    close_duration_same_channel = official(VIDEO_C, "Beta Gamma Delta", 191)
    reply, card = flag(world, official(VIDEO_B, "Alpha Beta Gamma", 190), similar_elsewhere, close_duration_same_channel)
    assert f"#{FIRST_KNOWN_ORDER}" in card and f"#{FIRST_KNOWN_ORDER + 1}" in card and NEAR_DUPLICATE_ADVICE not in card
    assert SIMILAR_REVIEW_REPLY in reply


def test_a_clean_case_shows_neither_the_advice_nor_any_concern_sentence(world) -> None:
    """@description: Nothing is added to a case that needed no comparison result (here: approved by the system, so no card exists).
    @param world: Comparing world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    assert SIMILAR_REVIEW_REPLY not in format_share_outcome("discord:2", outcome)
    assert all(row.submitter_id != BOB for row in world.owner.list_pending(OWNER_ID, after_sequence=None, limit=20).items)


def test_the_doubt_line_for_a_flagged_case_never_shows_a_title_or_a_channel_of_another_song(world) -> None:
    """@description: Only the plain sentences and the song number appear.
    @param world: Comparing world.
    @return: None.
    """
    old = official(VIDEO_A, f"{TITLE_CANARY} Alpha Beta Gamma", 189, TRUSTED)
    _reply, card = flag(world, official(VIDEO_B, "alpha beta gamma (Official Video)", 190), old)
    assert TITLE_CANARY not in card and TRUSTED not in card
