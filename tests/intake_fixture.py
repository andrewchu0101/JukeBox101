"""Seed legacy-style intake rows for offline tests, bypassing the public intake identity gate on purpose.

Test fixtures only: production code must never import this module. The helper runs the same transaction
and accept_submission call as SubmissionRepository.accept, so fixture rows match what intake would write.
"""

from bot.database.submission_store import accept_submission
from bot.database.submission_transaction import submission_transaction
from bot.submission_workflow import SubmissionRequest


def seed_intake(repository, request: SubmissionRequest):
    """@description: Write one fixture intake row through the same transaction path, for tests that model prior data.
    @param repository: Real SubmissionRepository over a fake or MySQL connection factory.
    @param request: Synthetic intake request, which may carry a seeded identity assertion.
    @return: Persisted submission result, as the public accept would return it.
    """
    with submission_transaction(repository._factory) as (cursor, control):
        return accept_submission(cursor, control, request)
