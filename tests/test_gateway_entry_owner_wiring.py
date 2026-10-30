"""Verify the formal Gateway entry registers the Owner review group with the same repository type the Owner CLI uses, and enforces the Owner gate and confirmation rules.

The production entry builds the real client; login is replaced by a recorder and the MySQL boundary by the offline fake database. Nothing connects to Discord or MySQL.
"""

import asyncio
import importlib
from datetime import datetime, timedelta, timezone

import discord
import pytest

from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.owner_review import ExpiredReviewToken, OwnerReviewService, ReviewAction
from bot.review import ReviewSnapshot, StaleReviewVersion
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import VersionAssignment
from tests.intake_fixture import seed_intake
from tests.submission_persistence_fake import FakeDatabase
from tests.test_discord_owner_review import FakeInteraction, _command, _refusal_texts, _run

OWNER_ID = "123"  # [Owner snowflake text]: Matches ENTRY_SETTINGS in the environment.
GUILD_ID = 456  # [Owner management guild]: Matches DISCORD_OWNER_GUILD_ID.
OTHER_GUILD_ID = 789  # [wrong guild]: Any guild that is not the private one.
OTHER_USER_ID = 999  # [non-Owner]: Any user that is not the configured Owner.
OPERATION_ID = "op-1"  # [seeded operation]: Accepted intake the Owner reviews.
START = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)  # [synthetic clock start]: Fixed instant for previews.
ENTRY_SETTINGS = {
    "DISCORD_BOT_TOKEN": "SYNTHETIC-TOKEN-0123456789",
    "DISCORD_OWNER_GUILD_ID": str(GUILD_ID),
    "DISCORD_EXPECTED_APPLICATION_ID": "789001",
    "DISCORD_OWNER_USER_ID": OWNER_ID,
    "DB_HOST": "db.synthetic.invalid",
    "DB_PORT": "5432",
    "DB_NAME": "jukebox_synthetic",
    "DB_USER": "jukebox_app",
    "DB_PASSWORD": "SYNTHETIC-DB-PASSWORD-9876",
}


class Clock:
    """Synthetic time source shared by the entry wiring and the test body."""

    def __init__(self) -> None:
        """@description: Start at a fixed instant so expiry is deterministic.
        @param: none
        @return: None.
        """
        self.current = START  # [synthetic now]: Moved forward explicitly by the test.

    def __call__(self) -> datetime:
        """@description: Report the current synthetic instant to the service.
        @param: none
        @return: Timezone-aware instant.
        """
        return self.current


@pytest.fixture
def wired(monkeypatch):
    """@description: Run the production entry with an offline database, a recorded login and a controllable clock, then expose the captured client and service.
    @param monkeypatch: Environment and boundary isolation facility.
    @return: Namespace with the captured client, the review service, the clock and the fake database.
    """
    entry = importlib.import_module("bot.__main__")
    for name, value in ENTRY_SETTINGS.items():
        monkeypatch.setenv(name, value)
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)
    clock = Clock()
    captured = {}
    created_services = []

    def fake_run(self, token, **kwargs):
        """@description: Keep the client that the entry would log in with, without contacting Discord.
        @param self: Client built by the entry.
        @param token: Credential passed to login.
        @param kwargs: Ignored keyword options.
        @return: None.
        """
        captured["client"] = self

    class RecordingService(OwnerReviewService):
        """Keep a handle on the service the entry builds so the test can drive the same instance."""

        def __init__(self, repository, owner_id, clock_source, token_ttl=timedelta(minutes=5)):
            """@description: Build the real service and remember it.
            @param repository: Repository the entry supplied.
            @param owner_id: Configured Owner identifier.
            @param clock_source: Clock the entry supplied.
            @param token_ttl: Preview lifetime the entry supplied.
            @return: None.
            """
            super().__init__(repository, owner_id, clock_source, token_ttl=token_ttl)
            created_services.append(self)

    monkeypatch.setattr(discord.Client, "run", fake_run)
    monkeypatch.setattr("psycopg.connect", lambda *args, **kwargs: database.connect())
    monkeypatch.setattr(entry, "SubmissionRepository", lambda _factory, owner_id: SubmissionRepository(database.connect, owner_id))
    monkeypatch.setattr(entry, "OwnerReviewService", RecordingService)
    monkeypatch.setattr(entry, "_utc_now", clock)
    entry.main()
    _seed_operation(SubmissionRepository(database.connect, int(OWNER_ID)))
    return _Wired(captured["client"], created_services[0], clock, database)


class _Wired:
    """Carry the production client, service, clock and fake database for one test."""

    def __init__(self, client, service, clock, database) -> None:
        """@description: Hold the collaborators the production entry produced.
        @param client: Production client with its command tree.
        @param service: Review service the entry built.
        @param clock: Synthetic clock.
        @param database: Offline fake database.
        @return: None.
        """
        self.client = client
        self.service = service
        self.clock = clock
        self.database = database


def _seed_operation(repository: SubmissionRepository) -> None:
    """@description: Write one accepted intake so the Owner has a real operation to review.
    @param repository: Repository over the offline fake database.
    @return: None.
    """
    video = VideoIdentity("Synth0001X1")
    seed_intake(repository, SubmissionRequest(f"https://youtu.be/{video.video_id}", OPERATION_ID, 101, 999,
                                              VersionAssignment(video), ReviewSnapshot(video), 0,
                                              DurationEvidence(video, 180, "synthetic-duration", True, True)))


def _owner_group(client, guild_id: int = GUILD_ID):
    """@description: Fetch the owner group registered on the client for one guild.
    @param client: Production client.
    @param guild_id: Guild whose command scope is inspected.
    @return: The owner app command group.
    """
    return client.tree.get_command("owner", guild=discord.Object(id=guild_id))


def _list_as(client, user_id: int, guild_id: int | None) -> FakeInteraction:
    """@description: Invoke the owner list subcommand through the real command tree as one caller.
    @param client: Production client.
    @param user_id: Caller snowflake.
    @param guild_id: Guild the call comes from.
    @return: Fake interaction recording the replies.
    """
    interaction = FakeInteraction(user_id=user_id, guild_id=guild_id)
    _run(_command(_owner_group(client), "list").callback(interaction))
    return interaction


def _current_version(service: OwnerReviewService) -> int:
    """@description: Read the case version the Owner would see on screen before deciding.
    @param service: Review service the entry built.
    @return: Current review version of the seeded operation.
    """
    return service.view(int(OWNER_ID), OPERATION_ID).review.version


def _preview_reject(service: OwnerReviewService):
    """@description: Issue a reject preview against the seeded operation at the version the Owner currently sees.
    @param service: Review service the entry built.
    @return: The preview with its single-use token.
    """
    return service.preview(int(OWNER_ID), OPERATION_ID, ReviewAction.REJECT, "不是歌曲", _current_version(service), "ref-1")


def test_owner_group_is_registered_only_for_the_private_guild(wired):
    """@description: The production entry exposes /owner for the configured private guild and nowhere else.
    @param wired: Production client and offline collaborators.
    @return: None.
    """
    assert _owner_group(wired.client) is not None
    assert wired.client.tree.get_command("owner") is None


def test_the_entry_uses_the_owner_cli_service_and_repository_types(wired):
    """@description: The entry's review service is the same class the Owner CLI builds, over the submission repository type.
    @param wired: Production client and offline collaborators.
    @return: None.
    """
    assert isinstance(wired.service, OwnerReviewService)
    assert isinstance(wired.service._repository, SubmissionRepository)


@pytest.mark.parametrize(("user_id", "guild_id"), [(OTHER_USER_ID, GUILD_ID), (int(OWNER_ID), OTHER_GUILD_ID)])
def test_non_owner_and_wrong_guild_get_the_same_refusal(wired, user_id, guild_id):
    """@description: A non-Owner or a caller from another guild receives exactly the refusal text, revealing nothing about the case.
    @param wired: Production client and offline collaborators.
    @param user_id: Caller snowflake under test.
    @param guild_id: Guild the caller used.
    @return: None.
    """
    refused = _list_as(wired.client, user_id, guild_id)
    reference = _list_as(wired.client, OTHER_USER_ID, OTHER_GUILD_ID)
    assert _refusal_texts(refused) == _refusal_texts(reference)
    assert len(_refusal_texts(refused)) == 1


def test_owner_in_the_private_guild_reaches_the_entry_service(wired):
    """@description: The Owner in the private guild lists the seeded operation through the production wiring.
    @param wired: Production client and offline collaborators.
    @return: None.
    """
    listing = _list_as(wired.client, int(OWNER_ID), GUILD_ID)
    assert any(OPERATION_ID in str(call[1]) for call in listing.followup.calls)


def test_expired_preview_is_refused_and_writes_nothing(wired):
    """@description: A preview older than the entry's confirmation window is refused and leaves the review version unchanged.
    @param wired: Production client and offline collaborators.
    @return: None.
    """
    initial_version = _current_version(wired.service)
    preview = _preview_reject(wired.service)
    wired.clock.current = START + timedelta(minutes=6)
    with pytest.raises(ExpiredReviewToken):
        wired.service.confirm(int(OWNER_ID), preview.token_id)
    assert _current_version(wired.service) == initial_version


def test_preview_is_refused_after_the_case_version_changed(wired):
    """@description: When another decision moves the case version first, the older preview is refused and nothing is written.
    @param wired: Production client and offline collaborators.
    @return: None.
    """
    initial_version = _current_version(wired.service)
    stale_preview = _preview_reject(wired.service)
    fresh_preview = _preview_reject(wired.service)
    wired.service.confirm(int(OWNER_ID), fresh_preview.token_id)
    with pytest.raises(StaleReviewVersion):
        wired.service.confirm(int(OWNER_ID), stale_preview.token_id)
    assert _current_version(wired.service) == initial_version + 1
