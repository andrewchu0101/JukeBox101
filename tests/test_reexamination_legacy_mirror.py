"""Prove a formal reexamination that freezes a confirmed identity also updates the legacy identity mirror.

Synthetic data over the offline SQL-protocol fake only; no MySQL, Discord or Gateway is involved.
"""

from bot.database.submission_codec import encode_contract
from bot.database.submission_store import _upsert_pending_song
from bot.database.submission_transaction import submission_transaction
from bot.urls import parse_youtube_url
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


def test_formal_reexamination_that_freezes_a_confirmed_identity_updates_the_legacy_mirror(world):
    """@description: A legacy-linked video whose identity becomes CONFLICT through reexamination also mirrors that CONFLICT into jb2_legacy.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    with submission_transaction(database.connect) as (cursor, _control):
        _upsert_pending_song(cursor, parse_youtube_url(f"https://youtu.be/{video_of(1).video_id}"))  # [legacy song]: A songs row that existed before intake links jb2_legacy.
    seed_intake(repository, submission(1))
    approve(service, 1)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview_reexamination(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-b", "orig-B", hours=1),
                                          "synthetic reexamination", revision).token_id
    service.confirm(OWNER_ID, token)
    current = service.view(OWNER_ID, "op-1").identity
    (legacy,) = database.state["jb2_legacy"].values()
    assert current.status.value == "conflict"
    assert legacy["identity_json"] == encode_contract(current)
