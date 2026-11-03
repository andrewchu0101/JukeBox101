"""Real PostgreSQL behaviour of the Owner's yes/no answer: one press pays, no rejects or records a duplicate, and a frozen conflict stays frozen.

The world is the music_category world of the other Owner tests; the decision core runs against the real review service, repository and settlement.
"""

from dataclasses import replace

import pytest

from bot.owner_decisions import Answer, DecisionKind, case_choices, decide
from bot.owner_review import ReviewAction
from bot.review import ReviewStatus
from bot.song_judgement import ContentJudgement
from bot.version_identity import VersionIdentityStatus
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, NOW, OWNER_ID, VIDEO_A, VIDEO_B
from tests.test_postgres_auto_version_confirm import official
from tests.test_postgres_conflict_resolution import conflicted_video, identity_of
from tests.test_postgres_music_category_mode import CHANNEL_A, MusicWorld

pytestmark = pytest.mark.postgres

NOT_MUSIC = "24"  # [category gate]: A non-music category sends the case to the Owner without any version doubt.


@pytest.fixture
def world(pg_database) -> MusicWorld:
    """@description: Provide one music_category world with settlement on.
    @param pg_database: Disposable database.
    @return: World.
    """
    return MusicWorld(pg_database)


def waiting_for_owner(world: MusicWorld, user: int, video: str, operation: str) -> None:
    """@description: Share a video that the category gate sends to the Owner.
    @param world: World.
    @param user: Submitter.
    @param video: Video.
    @param operation: Operation key.
    @return: None.
    """
    world.metadata.scripted[video] = replace(world.metadata.scripted[video], category_id=NOT_MUSIC)
    assert world.share(user, video, operation).review_status is ReviewStatus.PENDING_REVIEW


def pending(world: MusicWorld, operation: str):
    """@description: Read the case of an operation from the Owner's queue.
    @param world: World.
    @param operation: Operation key.
    @return: Pending row.
    """
    page = world.owner.list_pending(OWNER_ID)
    return next(row for row in page.items if row.operation_id == operation)


def owner_approves_only(world: MusicWorld, operation: str) -> None:
    """@description: Approve the song through the old single-step path, leaving the duplicate check open.
    @param world: World.
    @param operation: Operation key.
    @return: None.
    """
    item = pending(world, operation)
    video = world.owner.view(OWNER_ID, operation).review.video
    citation = f"owner-card:{video.video_id}"
    content = ContentJudgement(video=video, judged_by=OWNER_ID, reason="Owner 核准", retrieved_at=NOW, evidence_reference=citation, is_song=True)
    preview = world.owner.preview(OWNER_ID, operation, ReviewAction.APPROVE, "Owner 核准", item.revision, citation, content)
    world.owner.confirm(OWNER_ID, preview.token_id)


def test_yes_on_a_waiting_case_approves_confirms_and_pays_one_nickel(world) -> None:
    """@description: One answer ends with the submitter +1 and balanced books.
    @param world: World.
    @return: None.
    """
    waiting_for_owner(world, ALICE, VIDEO_A, "discord:1")
    result = decide(world.owner, OWNER_ID, pending(world, "discord:1"), Answer.YES)
    assert result.kind is DecisionKind.PAID and result.paid and not result.still_pending
    assert world.balances() == {ALICE: 1}
    world.assert_books_balance(1)
    assert identity_of(world, "discord:1").status is VersionIdentityStatus.CONFIRMED


def test_yes_on_a_case_with_doubts_pays_too(world) -> None:
    """@description: A look-alike song is held for the Owner; the Owner's yes still completes and pays.
    @param world: World.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.metadata.scripted[VIDEO_B] = official(VIDEO_B, "alpha beta gamma (Official Video)", 190, CHANNEL_A)
    assert world.share(BOB, VIDEO_B, "discord:2").review_status is ReviewStatus.PENDING_REVIEW
    item = pending(world, "discord:2")
    assert case_choices(item).doubt_text and case_choices(item).can_yes
    assert decide(world.owner, OWNER_ID, item, Answer.YES).kind is DecisionKind.PAID
    assert world.balances() == {ALICE: 1, BOB: 1}
    world.assert_books_balance(2)


def test_no_rejects_without_a_reward_and_does_not_block_other_submitters(world) -> None:
    """@description: The rejected submitter gets nothing, and another person's later song is paid normally.
    @param world: World.
    @return: None.
    """
    waiting_for_owner(world, ALICE, VIDEO_A, "discord:1")
    result = decide(world.owner, OWNER_ID, pending(world, "discord:1"), Answer.NO)
    assert result.kind is DecisionKind.REJECTED and not result.paid
    assert world.balances().get(ALICE, 0) == 0
    assert world.share(BOB, VIDEO_B, "discord:2").review_status is ReviewStatus.APPROVED
    assert world.balances() == {ALICE: 0, BOB: 1}
    world.assert_books_balance(1)


def test_yes_on_an_approved_case_waiting_for_the_duplicate_check_pays(world) -> None:
    """@description: Only the missing second step runs and the reward is settled once.
    @param world: World.
    @return: None.
    """
    waiting_for_owner(world, ALICE, VIDEO_A, "discord:1")
    owner_approves_only(world, "discord:1")
    item = pending(world, "discord:1")
    assert case_choices(item).no_requires_duplicate_pick
    assert decide(world.owner, OWNER_ID, item, Answer.YES).kind is DecisionKind.PAID
    assert world.balances() == {ALICE: 1}
    world.assert_books_balance(1)


def test_no_on_an_approved_case_records_the_picked_song_as_the_same_and_pays_nothing(world) -> None:
    """@description: The case maps to an already accepted song: same identity, no reward, books unchanged.
    @param world: World.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    waiting_for_owner(world, BOB, VIDEO_B, "discord:2")
    owner_approves_only(world, "discord:2")
    first = world.owner.list_known_versions(OWNER_ID).items[0]
    result = decide(world.owner, OWNER_ID, pending(world, "discord:2"), Answer.NO, duplicate_of_sequence=first.first_sequence)
    assert result.kind is DecisionKind.DUPLICATE_RECORDED and not result.paid
    assert identity_of(world, "discord:2").status is VersionIdentityStatus.CONFIRMED
    assert world.balances() == {ALICE: 1, BOB: 0}
    world.assert_books_balance(1)
    assert admin_query(world.database, "SELECT 1 FROM jb2_review_history WHERE decision_reason LIKE '%%rejected%%'") == []


def test_a_frozen_conflict_cannot_be_decided_and_pays_nothing(world) -> None:
    """@description: Neither answer is available, nothing is written, and the conflict stays.
    @param world: World.
    @return: None.
    """
    operation, _ = conflicted_video(world)
    item = pending(world, operation)
    assert case_choices(item).frozen_conflict
    for answer in (Answer.YES, Answer.NO):
        assert decide(world.owner, OWNER_ID, item, answer).kind is DecisionKind.REFUSED
    assert identity_of(world, operation).status is VersionIdentityStatus.CONFLICT
    assert world.balances().get(ALICE, 0) == 0


# ---- rules carried over from the retired version-flow and concern-wording tests ----

OTHER_CHANNEL = "UC" + "y" * 22  # [second channel]: Never matches the candidate's channel.
TITLE_CANARY = "TITLE-CANARY-PG"  # [official title marker]: Must never reach the doubt text.
ADVICE = "高度相同，建議回答「不是」"  # [advice]: Added only when one song matches on channel, duration and title.


def flagged(world: MusicWorld, candidate, *old):
    """@description: Submit the old songs, then the candidate that the comparison sends to the Owner, and return the candidate's case.
    @param world: World.
    @param candidate: Metadata of the candidate (submitted last by BOB).
    @param old: Metadata of the existing songs, submitted first by ALICE.
    @return: Pending row and the video-to-sequence map the shell would build.
    """
    for index, metadata in enumerate(old, start=1):
        world.metadata.scripted[metadata.video_id] = metadata
        world.share(ALICE, metadata.video_id, f"discord:{index}")
    world.metadata.scripted[candidate.video_id] = candidate
    operation = f"discord:{len(old) + 1}"
    world.share(BOB, candidate.video_id, operation)
    known = world.owner.list_known_versions(OWNER_ID).items
    return pending(world, operation), {version.first_video_id: version.first_sequence for version in known}


def test_the_doubt_text_for_a_look_alike_names_the_song_and_advises_no_without_leaking(world) -> None:
    """@description: Same channel, one second apart, near-identical title: both reasons, the song number and the advice, and no title or channel.
    @param world: World.
    @return: None.
    """
    old = official(VIDEO_A, f"{TITLE_CANARY} Alpha Beta Gamma", 189, CHANNEL_A)
    item, lookup = flagged(world, official(VIDEO_B, f"{TITLE_CANARY} alpha beta gamma (Official Video)", 190, CHANNEL_A), old)
    text = case_choices(item, lookup).doubt_text
    assert ADVICE in text and "歌名相似" in text and "同頻道" in text and f"#{lookup[VIDEO_A]}" in text
    assert TITLE_CANARY not in text and CHANNEL_A not in text and VIDEO_A not in text


def test_a_version_keyword_alone_gives_a_plain_line_with_no_song_and_no_advice(world) -> None:
    """@description: The keyword doubt names no existing song.
    @param world: World.
    @return: None.
    """
    item, lookup = flagged(world, official(VIDEO_B, "Alpha Beta Gamma (Live)", 400, CHANNEL_A), official(VIDEO_A, "Zzzz Qqqq 1234", 189, CHANNEL_A))
    text = case_choices(item, lookup).doubt_text
    assert "歌名有" in text and ADVICE not in text and "#" not in text


def test_doubts_about_two_different_songs_give_no_advice(world) -> None:
    """@description: A similar title to one song and a close duration to another is not highly identical to either.
    @param world: World.
    @return: None.
    """
    similar = official(VIDEO_A, "Alpha Beta Gamma", 300, OTHER_CHANNEL)
    close = official("ccccccccccc", "Beta Gamma Delta", 191, CHANNEL_A)
    item, lookup = flagged(world, official(VIDEO_B, "Alpha Beta Gamma", 190, CHANNEL_A), similar, close)
    text = case_choices(item, lookup).doubt_text
    assert ADVICE not in text and "歌名相似" in text and "同頻道" in text


def test_two_owner_handlers_answering_yes_at_once_pay_exactly_once(world) -> None:
    """@description: Without a shared lock the database decides: one award, balanced books, and no handler reports a second payment.
    @param world: World.
    @return: None.
    """
    from concurrent.futures import ThreadPoolExecutor
    waiting_for_owner(world, ALICE, VIDEO_A, "discord:1")
    item = pending(world, "discord:1")

    def answer():
        """@description: Answer yes through one independent handler, returning the result or the exception.
        @param: none
        @return: Result or exception.
        """
        try:
            return decide(world.owner, OWNER_ID, item, Answer.YES, tokens={})
        except Exception as error:  # [race loser]: Any refusal is acceptable as long as nothing is paid twice.
            return error

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda _: answer(), range(2)))
    assert any(getattr(outcome, "kind", None) is DecisionKind.PAID for outcome in outcomes)
    assert world.balances() == {ALICE: 1}
    world.assert_books_balance(1)


def test_an_unknown_outcome_is_replayed_with_the_same_token_and_pays_once(world, monkeypatch) -> None:
    """@description: The commit succeeds but its answer is lost; the retry confirms the same token and the books show one award.
    @param world: World.
    @param monkeypatch: Patch helper.
    @return: None.
    """
    from bot.owner_review import ReviewOutcomeUnknown
    waiting_for_owner(world, ALICE, VIDEO_A, "discord:1")
    item, tokens = pending(world, "discord:1"), {}
    real_confirm, lost = world.owner.confirm, []

    def lossy(actor_id, token_id):
        """@description: Commit for real, then lose the answer once.
        @param actor_id: Caller.
        @param token_id: Token.
        @return: Snapshot on later calls.
        """
        snapshot = real_confirm(actor_id, token_id)
        if not lost:
            lost.append(token_id)
            raise ReviewOutcomeUnknown("lost")
        return snapshot

    monkeypatch.setattr(world.owner, "confirm", lossy)
    assert decide(world.owner, OWNER_ID, item, Answer.YES, tokens=tokens).kind is DecisionKind.UNKNOWN_OUTCOME
    assert decide(world.owner, OWNER_ID, item, Answer.YES, tokens=tokens).kind is DecisionKind.PAID
    assert world.balances() == {ALICE: 1}
    world.assert_books_balance(1)
