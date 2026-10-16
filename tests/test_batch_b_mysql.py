"""Exercise migrated Batch B repositories against opt-in real MySQL 8 transactions.

The independent harness owns setup and precise teardown; business operations never use its credentials or a fake database.
"""

from collections.abc import Iterator
import json
import os
import pytest
from tests.batch_b_harness import MySQLHarness
from tests.batch_b_harness_contract import RUN_SWITCH, HARNESS_SWITCH, load_profiles

pytestmark = pytest.mark.skipif(os.environ.get(RUN_SWITCH) != "1" or os.environ.get(HARNESS_SWITCH) != "1",
                                reason="Real Batch B MySQL requires both explicit application/harness opt-ins")


@pytest.fixture(scope="session")
def batch_b_write_guard() -> dict:
    """@description: Stop subsequent real writes after any uncertain setup or cleanup failure.
    @param: none
    @return: Session-local poison state; cross-process exclusivity is enforced by MySQL advisory lock.
    """
    return {"blocked": False}


@pytest.fixture
def mysql_batch_b(batch_b_write_guard: dict) -> Iterator[MySQLHarness]:
    """@description: Restore the accepted empty baseline even when a real scenario assertion fails.
    @param batch_b_write_guard: Session poison state shared across tests.
    @return: Enabled harness whose finally path cleans only verified owned rows.
    """
    if batch_b_write_guard["blocked"]:
        pytest.fail("HARNESS_SESSION_BLOCKED_AFTER_PREVIOUS_FAILURE")
    application, management = load_profiles()
    harness = MySQLHarness(application, management)  # [one test run]: All roots are journaled before enabling switches.
    try:
        harness.start()
        yield harness
    except BaseException:
        batch_b_write_guard["blocked"] = True
        raise
    finally:
        try:
            harness.finish()
            if harness.failed:
                batch_b_write_guard["blocked"] = True
        except BaseException:
            batch_b_write_guard["blocked"] = True
            pytest.fail("HARNESS_RECOVERY_FAILED; inspect nonsecret journal: " + str(harness.journal), pytrace=False)
        finally:
            print(json.dumps({"run_id": harness.scope.run_id, "journal": str(harness.journal), "restoration_pending": harness.enabled,
                              "counts": harness.last_counts, "session_blocked": batch_b_write_guard["blocked"]}))


@pytest.mark.parametrize("scenario", ["persistence", "priority", "rejected", "concurrent_accept", "concurrent_reward",
                                     "concurrent_retry", "lock_timeout", "owner_review", "conflict", "remix_merge"])
def test_real_batch_b_workflow(mysql_batch_b: MySQLHarness, scenario: str) -> None:
    """@description: Verify genuine MySQL persistence, ownership, concurrency and accounting invariants.
    @param mysql_batch_b: Verified and enabled isolated run.
    @param scenario: Reviewed bounded real-transaction scenario.
    @return: None.
    """
    outcome = mysql_batch_b.execute(scenario)  # [real worker outcome]: Errors contain only scenario, class and numeric driver code.
    assert outcome["status"] == "PASS", outcome


@pytest.mark.parametrize("prefix", ["INSERT INTO jb2_events", "INSERT INTO jb2_receipts", "INSERT INTO jb2_evidence",
                                    "INSERT INTO credit_ledger", "INSERT INTO jb2_claims", "UPDATE users SET credit_balance",
                                    "UPDATE jb2_events SET reward_status", "INSERT INTO jb2_settlements"])
def test_real_batch_b_rollback(mysql_batch_b: MySQLHarness, prefix: str) -> None:
    """@description: Check complete rollback after actual SQL execution at each critical transactional boundary.
    @param mysql_batch_b: Verified run with application-only repository workers.
    @param prefix: Precise after-execute client fault location.
    @return: None.
    """
    outcome = mysql_batch_b.execute("rollback:" + prefix)
    assert outcome["status"] == "PASS", outcome


@pytest.mark.parametrize("mode", ["accept:before", "accept:after", "settle:before", "settle:after"])
def test_real_batch_b_unknown_commit(mysql_batch_b: MySQLHarness, mode: str) -> None:
    """@description: Reuse idempotency keys after injected client acknowledgement loss around genuine MySQL commits.
    @param mysql_batch_b: Verified real run, never FakeDatabase.
    @param mode: Commit ambiguity branch and business operation.
    @return: None.
    """
    outcome = mysql_batch_b.execute("uncertain:" + mode)
    assert outcome["status"] == "PASS", outcome


def test_real_batch_b_reminder(mysql_batch_b: MySQLHarness) -> None:
    """@description: Keep clock setup on the harness and all reminder business decisions on the application account.
    @param mysql_batch_b: Registered pending run.
    @return: None.
    """
    assert mysql_batch_b.execute("reminder_seed")["status"] == "PASS"
    mysql_batch_b.make_reminders_due()
    assert mysql_batch_b.execute("reminder_read")["status"] == "PASS"
