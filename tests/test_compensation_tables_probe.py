"""Verify the offline behaviour of the compensation-table probe: all absent is False, complete is True, a partial set refuses with the missing names, and a failing query propagates.

The probe decides whether a switched-off claim must still be guarded. Nothing here connects to MySQL.
"""

import pytest

from bot.database.compensation_schema import CompensationSchemaMissing, compensation_tables_present
from tests.compensation_sqlite import create_database
from tests.test_compensation_claim_guard import TABLES, drop
from tests.test_compensation_store import transaction


def test_the_probe_reports_all_tables_absent_as_false(tmp_path) -> None:
    """@description: With none of the three tables present the probe reports absence, so an ordinary deployment is not guarded.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "probe-absent.sqlite"
    create_database(path)
    drop(path, TABLES)
    with transaction(path) as cursor:
        assert compensation_tables_present(cursor) is False


def test_the_probe_reports_a_complete_set_as_true(tmp_path) -> None:
    """@description: With all three tables present the probe reports presence, so the claim is guarded even while the switch is off.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "probe-complete.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        assert compensation_tables_present(cursor) is True


def test_the_probe_refuses_a_partial_set_and_names_only_the_missing_tables(tmp_path) -> None:
    """@description: A partial set raises, and the message names only the absent table, never the present ones.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "probe-partial.sqlite"
    create_database(path)
    drop(path, ("exchange_incident_events",))
    with transaction(path) as cursor:
        with pytest.raises(CompensationSchemaMissing) as refusal:
            compensation_tables_present(cursor)
    assert "exchange_incident_events" in str(refusal.value)
    assert "exchange_incidents" not in str(refusal.value)


class FailingCursor:
    """Stand in for a cursor whose catalogue query fails, as a lock timeout or a lost connection would."""

    def execute(self, sql: str, params=()) -> None:
        """@description: Fail every statement the way a broken connection does.
        @param sql: Statement text, unused.
        @param params: Bound values, unused.
        @return: None; always raises.
        """
        raise RuntimeError("catalogue query failed")


def test_a_failing_catalogue_query_propagates_and_is_never_read_as_absent() -> None:
    """@description: A failed probe raises instead of reporting absence, so a claim can never proceed past an unknown catalogue.
    @return: None.
    """
    with pytest.raises(RuntimeError, match="catalogue query failed"):
        compensation_tables_present(FailingCursor())
