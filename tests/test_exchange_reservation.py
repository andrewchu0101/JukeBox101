"""Verify exchange reservation debits exactly once, excludes ineligible versions and rolls back as one unit.

Synthetic submissions over the offline SQL-protocol fake only; no MySQL, Discord, environment file or network is involved.
"""

import random
from dataclasses import replace

import pytest

from bot.database.exchange_reservation import ExchangeStatus
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.version_identity import VersionExclusion
from tests.intake_fixture import seed_intake
from tests.test_identity_service_path import (  # noqa: F401  (world is a pytest fixture imported by name)
    OWNER_ID,
    approve,
    confirm_version,
    owner_evidence,
    submission,
    video_of,
    world,
)

ORIGINAL_A = "orig-A"
ORIGINAL_B = "orig-B"
ORIGINAL_C = "orig-C"
RNG_SEED = 20261023  # [reviewed seed]: Only used to construct the deterministic draw source.


def settled_submitter(database, service, repository, number: int, original: str, submitter: int | None = None) -> None:
    """@description: Accept one synthetic submission, confirm its version, approve it and settle its first reward.
    @param database: Offline fake database.
    @param service: Owner review service over the fake.
    @param repository: Real repository over the fake.
    @param number: Synthetic submission number; the default submitter is 100 plus this number.
    @param original: Synthetic canonical recording version for this submission.
    @param submitter: Optional synthetic submitter overriding the default, used to give one user a second award.
    @return: None.
    """
    request = submission(number) if submitter is None else replace(submission(number), submitter_id=submitter)
    seed_intake(repository, request)
    confirm_version(service, number, owner_evidence(video_of(number), f"ev-{number}", original))
    approve(service, number)
    assert repository.settle(f"op-{number}").status.value == "awarded"


def verify_all_songs(database) -> None:
    """@description: Mark every synthetic song as verified with a link, the only deliverable state.
    @param database: Offline fake database whose committed state is adjusted for the test.
    @return: None.
    """
    for row in database.state["songs"].values():
        row.update(resolution_status="verified", youtube_url="https://www.youtube.com/watch?v=" + row["platform_track_id"],
                   verification_provenance="synthetic-verified")


def balance(database, user_id: int) -> int:
    """@description: Read one committed synthetic balance for assertions.
    @param database: Offline fake database.
    @param user_id: Synthetic Discord user identifier.
    @return: Committed credit balance.
    """
    return database.state["users"][user_id]["credit_balance"]


class FirstPicks(random.Random):
    """@description: Always draw the first eligible version in canonical order, so a missing exclusion is observable rather than hidden by chance.
    @param: none
    @return: Random source whose choice is deterministic.
    """

    def choice(self, seq):
        """@description: Return the first eligible element of the canonical pool.
        @param seq: Eligible versions in canonical order.
        @return: The first element.
        """
        return seq[0]


def exchange(repository, user_id: int, key: str):
    """@description: Call the production exchange entry with a deterministic draw that exposes any missing exclusion.
    @param repository: Real repository over the fake.
    @param user_id: Requesting synthetic user.
    @param key: Request idempotency key.
    @return: Exchange outcome.
    """
    return repository.exchange(user_id, key, FirstPicks(RNG_SEED))


@pytest.fixture
def two_submitters(world):
    """@description: Two users who each hold one settled, distinct recording version and one point.
    @param world: Offline database, repository and service.
    @return: Tuple of database, repository and service after setup.
    """
    database, repository, service = world
    settled_submitter(database, service, repository, 1, ORIGINAL_A)
    settled_submitter(database, service, repository, 2, ORIGINAL_B)
    verify_all_songs(database)
    return database, repository, service


def test_reservation_debits_one_point_and_delivers_the_other_users_verified_link(two_submitters) -> None:
    """@description: The main flow reserves the only eligible other-user version and debits exactly one point in the same commit.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    outcome = exchange(repository, 101, "exchange-1")
    assert outcome.status is ExchangeStatus.RESERVED
    assert outcome.youtube_url == "https://www.youtube.com/watch?v=" + video_of(2).video_id
    assert balance(database, 101) == 0
    assert len(database.state["recommendations"]) == 1
    debit = next(row for row in database.state["credit_ledger"].values() if row["reason"] == "exchange_debit")
    assert debit["credit_delta"] == -1 and debit["recommendation_id"] == outcome.recommendation_id


def test_insufficient_credits_charge_nothing_and_create_no_recommendation(two_submitters) -> None:
    """@description: A user with no spendable point gets an insufficient-credits outcome with no write at all.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    seed_intake(repository, submission(3))
    before = (dict(database.state["users"][103]), len(database.state["credit_ledger"]))
    outcome = exchange(repository, 103, "exchange-broke")
    assert outcome.status is ExchangeStatus.INSUFFICIENT_CREDITS
    assert (dict(database.state["users"][103]), len(database.state["credit_ledger"])) == before
    assert database.state["recommendations"] == {}


def test_no_candidate_charges_nothing_when_only_the_requesters_own_version_is_settled(world) -> None:
    """@description: If the only settled version belongs to the requester, the outcome is no candidate and the point stays.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    settled_submitter(database, service, repository, 1, ORIGINAL_A)
    verify_all_songs(database)
    outcome = exchange(repository, 101, "exchange-own-only")
    assert outcome.status is ExchangeStatus.NO_CANDIDATE
    assert balance(database, 101) == 1 and database.state["recommendations"] == {}


def test_unverified_song_is_never_delivered(two_submitters) -> None:
    """@description: A settled version whose song is not verified is not recommended, so nothing is reserved or charged.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.state["songs"][video_of(2).video_id]["resolution_status"] = "pending"
    outcome = exchange(repository, 101, "exchange-unverified")
    assert outcome.status is ExchangeStatus.NO_CANDIDATE
    assert balance(database, 101) == 1


def test_retry_with_the_same_key_returns_the_original_reservation_without_a_second_debit(two_submitters) -> None:
    """@description: A technical retry replays the original reservation; it neither debits again nor creates a second recommendation.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    first = exchange(repository, 101, "exchange-retry")
    second = exchange(repository, 101, "exchange-retry")
    assert second.status is ExchangeStatus.REPLAYED
    assert second.recommendation_id == first.recommendation_id and second.youtube_url == first.youtube_url
    assert balance(database, 101) == 0 and len(database.state["recommendations"]) == 1
    assert sum(row["reason"] == "exchange_debit" for row in database.state["credit_ledger"].values()) == 1


def test_a_new_request_after_success_cannot_spend_the_same_point(two_submitters) -> None:
    """@description: After one successful reservation a different request key finds no spendable point and does not debit.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    first = exchange(repository, 101, "exchange-first")
    set_delivery_status(database, first.recommendation_id, "delivered")  # [completed delivery]: The next exchange waits until the first is delivered.
    outcome = exchange(repository, 101, "exchange-second")
    assert outcome.status is ExchangeStatus.INSUFFICIENT_CREDITS
    assert balance(database, 101) == 0 and len(database.state["recommendations"]) == 1


def test_a_failure_during_the_debit_leaves_no_partial_reservation(two_submitters) -> None:
    """@description: A statement failure inside the transaction rolls back the recommendation, ledger and balance together.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.sql_fault = "INSERT INTO credit_ledger"
    with pytest.raises(ValueError):
        exchange(repository, 101, "exchange-fault")
    assert balance(database, 101) == 1
    assert database.state["recommendations"] == {}
    assert not any(row["reason"] == "exchange_debit" for row in database.state["credit_ledger"].values())


def test_a_version_under_conflict_is_withdrawn_from_new_exchanges_without_revoking_its_award(world) -> None:
    """@description: Once a candidate of a settled version is in conflict, that version is no longer offered, and its prior award stays intact.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    settled_submitter(database, service, repository, 1, ORIGINAL_A)
    settled_submitter(database, service, repository, 2, ORIGINAL_B)
    settled_submitter(database, service, repository, 3, ORIGINAL_C)
    seed_intake(repository, submission(4))  # [unawarded sibling]: Same version as the settled B claim, still waiting.
    confirm_version(service, 4, owner_evidence(video_of(4), "ev-4", ORIGINAL_B))
    approve(service, 4)
    verify_all_songs(database)
    version_b = repository.review_view(OWNER_ID, "op-2").identity.version
    repository.exclude("proof-b", OWNER_ID, "evidence conflict", VersionExclusion(video_of(2), version_b, "contradictory-proof"))
    assert repository.settle("op-4").status is Status.CONFLICT
    outcome = exchange(repository, 101, "exchange-after-conflict")
    assert outcome.status is ExchangeStatus.RESERVED
    assert outcome.youtube_url == "https://www.youtube.com/watch?v=" + video_of(3).video_id
    assert len(database.state["jb2_claims"]) == 3 and database.state["users"][102]["credit_balance"] == 1


def test_a_song_already_held_is_excluded_while_another_eligible_version_remains(world) -> None:
    """@description: Once a song is held it cannot be drawn again, while a different eligible version can still be reserved.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    settled_submitter(database, service, repository, 1, ORIGINAL_A)
    settled_submitter(database, service, repository, 2, ORIGINAL_B)
    settled_submitter(database, service, repository, 3, ORIGINAL_C)
    settled_submitter(database, service, repository, 4, ORIGINAL_A + "-second", submitter=101)  # [second point]: same requester earns another award
    verify_all_songs(database)
    first = exchange(repository, 101, "exchange-held-1")
    set_delivery_status(database, first.recommendation_id, "delivered")  # [completed delivery]: The next exchange waits until the first is delivered.
    second = exchange(repository, 101, "exchange-held-2")
    set_delivery_status(database, second.recommendation_id, "delivered")  # [completed delivery]: The third request is judged after the second is delivered.
    assert first.status is ExchangeStatus.RESERVED and second.status is ExchangeStatus.RESERVED
    assert first.youtube_url != second.youtube_url
    held = {row["song_id"] for row in database.state["recommendations"].values()}
    assert held == {database.state["songs"][video_of(2).video_id]["song_id"], database.state["songs"][video_of(3).video_id]["song_id"]}
    third = exchange(repository, 101, "exchange-held-3")
    assert third.status is ExchangeStatus.INSUFFICIENT_CREDITS and balance(database, 101) == 0



@pytest.fixture
def three_candidates(world):
    """@description: Give user 101 two points and leave two other-user candidates, so a second debit would be observable as a second reservation.
    @param world: Offline database, repository and service.
    @return: Tuple of database, repository and service after setup.
    """
    database, repository, service = world
    settled_submitter(database, service, repository, 1, ORIGINAL_A)
    settled_submitter(database, service, repository, 2, ORIGINAL_B)
    settled_submitter(database, service, repository, 3, ORIGINAL_C)
    settled_submitter(database, service, repository, 4, "orig-D", submitter=101)
    verify_all_songs(database)
    return database, repository, service


def set_delivery_status(database, recommendation_id: int, status: str) -> None:
    """@description: Move one reservation to a delivery state the way the delivery path would, for the fake database.
    @param database: Offline fake database.
    @param recommendation_id: Reservation to move.
    @param status: Target delivery status.
    @return: None.
    """
    database.state["recommendations"][recommendation_id]["status"] = status


def test_a_new_request_key_is_refused_while_the_first_exchange_is_reserved(three_candidates) -> None:
    """@description: A second request with a different key cannot debit or reserve while the first reservation is still unfinished.
    @param three_candidates: Requester with two points and two other-user candidates.
    @return: None.
    """
    database, repository, _ = three_candidates
    first = exchange(repository, 101, "exchange-first")
    assert first.status is ExchangeStatus.RESERVED
    before = (balance(database, 101), len(database.state["credit_ledger"]), len(database.state["recommendations"]))
    second = exchange(repository, 101, "exchange-second")
    assert second.status is ExchangeStatus.IN_PROGRESS
    assert (balance(database, 101), len(database.state["credit_ledger"]), len(database.state["recommendations"])) == before


@pytest.mark.parametrize("open_status", ["sending", "uncertain"])
def test_sending_or_uncertain_delivery_also_refuses_a_new_key_without_a_debit(three_candidates, open_status: str) -> None:
    """@description: A delivery that is being sent or whose result is unknown keeps the requester blocked; an unknown result is never treated as finished.
    @param three_candidates: Requester with two points and two other-user candidates.
    @param open_status: Open delivery state under test.
    @return: None.
    """
    database, repository, _ = three_candidates
    first = exchange(repository, 101, "exchange-first")
    set_delivery_status(database, first.recommendation_id, open_status)
    second = exchange(repository, 101, "exchange-second")
    assert second.status is ExchangeStatus.IN_PROGRESS
    assert balance(database, 101) == 1 and len(database.state["recommendations"]) == 1


def test_a_completed_delivery_allows_the_next_exchange_from_the_remaining_points(three_candidates) -> None:
    """@description: Once the first reservation is delivered, the requester may exchange again with the point that remains.
    @param three_candidates: Requester with two points and two other-user candidates.
    @return: None.
    """
    database, repository, _ = three_candidates
    first = exchange(repository, 101, "exchange-first")
    set_delivery_status(database, first.recommendation_id, "delivered")
    second = exchange(repository, 101, "exchange-second")
    assert second.status is ExchangeStatus.RESERVED
    assert second.recommendation_id != first.recommendation_id
    assert balance(database, 101) == 0


def test_the_same_request_key_replays_without_a_second_debit_while_reserved(three_candidates) -> None:
    """@description: A technical retry of the same request returns the original reservation and never debits or reserves again.
    @param three_candidates: Requester with two points and two other-user candidates.
    @return: None.
    """
    database, repository, _ = three_candidates
    first = exchange(repository, 101, "exchange-first")
    replay = exchange(repository, 101, "exchange-first")
    assert replay.status is ExchangeStatus.REPLAYED and replay.recommendation_id == first.recommendation_id
    assert balance(database, 101) == 1 and len(database.state["recommendations"]) == 1
