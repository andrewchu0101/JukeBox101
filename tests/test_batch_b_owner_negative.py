"""Offline negative tests for Owner-only review, merge, exclude and pending-summary authorization.

The repository is built with a connection-factory spy that records any request for a database connection. Non-owner calls must raise PermissionError before that request, and the configured Owner must reach the connection boundary. No MySQL server, network service or Discord client is contacted.
"""

import pytest

from bot.database.review_persistence import ReviewCommand, require_owner
from bot.database.submission_repository import SubmissionRepository
from bot.song_identity import VideoIdentity
from bot.version_identity import RecordingVersionIdentity, VersionExclusion

OWNER_DISCORD_USER_ID = 100000000000000001  # [configured Owner]: The only identity that may pass the boundary.
NON_OWNER_DISCORD_USER_ID = 100000000000000002  # [unauthorized actor]: Must never reach a connection.


class ConnectionRequested(Exception):
    """Mark that authorization passed and the repository asked for a database connection."""


def connection_spy() -> tuple:
    """@description: Build a connection factory that records requests and stops before any database work.
    @param: none
    @return: Factory callable and the list that records each connection request.
    """
    requests = []  # [connection evidence]: Any entry means authorization let the call reach the database boundary.

    def factory() -> None:
        """@description: Record the connection request and fail without opening any connection.
        @param: none
        @return: Never returns; always raises ConnectionRequested.
        """
        requests.append(True)
        raise ConnectionRequested('connection boundary reached')

    return factory, requests


def repository_with_spy() -> tuple:
    """@description: Create the production repository with the owner configured and a connection spy.
    @param: none
    @return: Repository and the list of recorded connection requests.
    """
    factory, requests = connection_spy()
    return SubmissionRepository(factory, OWNER_DISCORD_USER_ID), requests


def synthetic_exclusion() -> VersionExclusion:
    """@description: Build a well-formed exclusion so only authorization, not argument validation, decides the outcome.
    @param: none
    @return: Explicit exclusion of one synthetic video from one synthetic version.
    """
    return VersionExclusion(VideoIdentity('AAAAAAAAAAA'), RecordingVersionIdentity('synthetic-version'), 'synthetic:evidence')


def review_call(repo: SubmissionRepository, actor_id: object) -> object:
    """@description: Submit a well-formed Owner review intent for the given actor.
    @param repo: Repository under test.
    @param actor_id: Actor identity presented to the review boundary.
    @return: Whatever the repository returns or raises.
    """
    command = ReviewCommand('synthetic:decision', 'synthetic:operation', actor_id, 'synthetic reason', 1,
                            song_eligible=True)
    return repo.review(command)


def merge_call(repo: SubmissionRepository, actor_id: object) -> object:
    """@description: Submit a well-formed Owner merge for the given actor.
    @param repo: Repository under test.
    @param actor_id: Actor identity presented to the merge boundary.
    @return: Whatever the repository returns or raises.
    """
    return repo.merge('synthetic:merge', actor_id, 'synthetic reason', 'synthetic-source', 'synthetic-target')


def exclude_call(repo: SubmissionRepository, actor_id: object) -> object:
    """@description: Submit a well-formed Owner exclusion for the given actor.
    @param repo: Repository under test.
    @param actor_id: Actor identity presented to the exclusion boundary.
    @return: Whatever the repository returns or raises.
    """
    return repo.exclude('synthetic:exclude', actor_id, 'synthetic reason', synthetic_exclusion())


def pending_call(repo: SubmissionRepository, actor_id: object) -> object:
    """@description: Request the Owner's pending summary for the given actor.
    @param repo: Repository under test.
    @param actor_id: Actor identity presented to the summary boundary.
    @return: Whatever the repository returns or raises.
    """
    return repo.pending_reminders(actor_id)


OWNER_OPERATIONS = [review_call, merge_call, exclude_call, pending_call]  # [protected operations]: Each must enforce the same Owner gate.
UNAUTHORIZED_ACTORS = [NON_OWNER_DISCORD_USER_ID, str(OWNER_DISCORD_USER_ID), None]  # [spoof attempts]: Wrong id, type-confused id and missing id.


@pytest.mark.parametrize('operation', OWNER_OPERATIONS, ids=lambda item: item.__name__)
@pytest.mark.parametrize('actor_id', UNAUTHORIZED_ACTORS, ids=['other_user', 'owner_as_string', 'missing'])
def test_non_owner_is_rejected_before_connection(operation, actor_id):
    """@description: Reject every non-owner actor with PermissionError before any database connection is requested.
    @param operation: Protected repository operation under test.
    @param actor_id: Unauthorized actor identity.
    @return: None.
    """
    repo, requests = repository_with_spy()
    with pytest.raises(PermissionError):
        operation(repo, actor_id)
    assert requests == []


@pytest.mark.parametrize('operation', OWNER_OPERATIONS, ids=lambda item: item.__name__)
def test_owner_passes_gate_and_reaches_connection_boundary(operation):
    """@description: Confirm the configured Owner passes authorization, so the negative results above are not blanket failures.
    @param operation: Protected repository operation under test.
    @return: None.
    """
    repo, requests = repository_with_spy()
    with pytest.raises(ConnectionRequested):
        operation(repo, OWNER_DISCORD_USER_ID)
    assert len(requests) == 1


def test_require_owner_rejects_other_identity():
    """@description: Check the shared gate directly, since every protected repository method delegates to it.
    @param: none
    @return: None.
    """
    with pytest.raises(PermissionError):
        require_owner(NON_OWNER_DISCORD_USER_ID, OWNER_DISCORD_USER_ID, 'synthetic reason')


def test_require_owner_rejects_string_spoof_of_owner_identity():
    """@description: Reject a string containing the Owner id, since the gate requires an exact int.
    @param: none
    @return: None.
    """
    with pytest.raises(PermissionError):
        require_owner(str(OWNER_DISCORD_USER_ID), OWNER_DISCORD_USER_ID, 'synthetic reason')


def test_require_owner_accepts_configured_owner():
    """@description: Accept exactly the configured Owner id with a reason, so the gate is not simply always closed.
    @param: none
    @return: None.
    """
    assert require_owner(OWNER_DISCORD_USER_ID, OWNER_DISCORD_USER_ID, 'synthetic reason') is None
