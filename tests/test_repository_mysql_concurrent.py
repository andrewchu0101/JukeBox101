"""Prepare bounded rollback-only concurrent submission acceptance scenarios.

Separate spawned workers contend on real database locks only after explicit repository test authorization.
"""

from uuid import uuid4

import pytest

from tests.repository_mysql_concurrency import SubmissionPlan, run_contention
from tests.repository_mysql_support import new_video, require_empty_tables, verified_counts
from tests.test_repository_mysql import repository_mysql_settings

pytestmark = pytest.mark.mysql


@pytest.mark.parametrize("same_submitter", [True, False])
def test_parallel_pending_lock_contention(repository_mysql_settings, same_submitter):
    """@description: Validate competing pending inserts without committing or deleting fixture data.
    @param repository_mysql_settings: Explicit fixed TEST_DB configuration.
    @param same_submitter: Contend on user identity too, or isolate song-identity contention.
    @return: None.
    """
    before_counts = verified_counts(repository_mysql_settings)
    require_empty_tables(before_counts)
    video = new_video()
    holder_user_id = uuid4().int >> 64
    contender_user_id = holder_user_id if same_submitter else uuid4().int >> 64
    holder_plan = SubmissionPlan(holder_user_id, video.source_url)
    contender_plan = SubmissionPlan(contender_user_id, video.source_url)
    try:
        run_contention(holder_plan, contender_plan)
    finally:
        assert verified_counts(repository_mysql_settings) == before_counts, "Six-table counts changed after concurrent acceptance"
