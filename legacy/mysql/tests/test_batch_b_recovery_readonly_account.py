"""Verify Recovery inspect connects only as the dedicated read-only account and refuses every other identity before connecting.

Driver, cursor and journal seams are replaced by in-memory spies; no database, environment file or network is touched.
"""

import hashlib
import io
import json
from unittest.mock import MagicMock

import pytest

from tests import batch_b_recovery as recovery
from tests.batch_b_harness_contract import HARNESS_USER, TABLES, HarnessSafetyError, RunScope
from tests.test_batch_b_recovery import journal_fixture

READONLY_USER = 'jukebox101_test_readonly'
FORBIDDEN_ACCOUNTS = [HARNESS_USER, 'jukebox101_test_app', 'jukebox101_test_migrator', 'root', '', ' ' + READONLY_USER]


class GrantCursor:
    """@description: Return fixed metadata rows for the exact statements the read-only verifier sends.
    @param grants: SHOW GRANTS rows as the driver returns them.
    @param role: Value returned by CURRENT_ROLE().
    @return: Cursor-like spy with execute and fetchall.
    """

    def __init__(self, grants: list, role: str = 'NONE') -> None:
        """@description: Store the synthetic metadata answers.
        @param grants: Lines returned for SHOW GRANTS.
        @param role: Role name returned for CURRENT_ROLE().
        @return: None.
        """
        self.grants = grants
        self.role = role
        self.rows = []

    def execute(self, sql: str, parameters: tuple = ()) -> int:
        """@description: Select the synthetic answer for one statement.
        @param sql: Statement text sent by the verifier.
        @param parameters: Unused bound values.
        @return: Row count placeholder.
        """
        if sql.startswith('SHOW GRANTS'):
            self.rows = [{'Grants for ' + READONLY_USER: line} for line in self.grants]
        else:
            self.rows = [{'roles': self.role}]
        return len(self.rows)

    def fetchall(self) -> tuple:
        """@description: Return the rows of the last statement.
        @param: none
        @return: Tuple of row dicts.
        """
        return tuple(self.rows)


def exact_grants() -> list:
    """@description: Build the reviewed grant set: usage only plus SELECT on each of the 23 tables.
    @param: none
    @return: Lines in the form the driver prints for SHOW GRANTS.
    """
    lines = ['GRANT USAGE ON *.* TO `' + READONLY_USER + '`@`127.0.0.1`']
    lines += [f'GRANT SELECT ON `jukebox101_test`.`{table}` TO `{READONLY_USER}`@`127.0.0.1`' for table in sorted(TABLES)]
    return lines


def test_reviewed_table_set_has_twenty_three_members() -> None:
    """@description: Pin the table count that the read-only grant check compares against.
    @param: none
    @return: None.
    """
    assert len(TABLES) == 23


def test_dedicated_account_name_is_the_only_recovery_readonly_identity() -> None:
    """@description: Fix the D-6 account name in one constant that the connection and identity check share.
    @param: none
    @return: None.
    """
    assert recovery.RECOVERY_READONLY_USER == READONLY_USER


@pytest.mark.parametrize('account', FORBIDDEN_ACCOUNTS)
def test_open_refuses_any_other_account_before_connecting(monkeypatch: pytest.MonkeyPatch, account: str) -> None:
    """@description: Refuse harness, application, migrator, blank and near-miss names before any driver call.
    @param monkeypatch: Driver spy.
    @param account: Requested account name.
    @return: None.
    """
    connect = MagicMock()
    monkeypatch.setattr(recovery.pymysql, 'connect', connect)
    with pytest.raises(HarnessSafetyError):
        recovery.open_recovery_readonly(account, 'synthetic-private')
    connect.assert_not_called()


def test_open_refuses_missing_password_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Refuse an empty credential before the driver is touched.
    @param monkeypatch: Driver spy.
    @return: None.
    """
    connect = MagicMock()
    monkeypatch.setattr(recovery.pymysql, 'connect', connect)
    with pytest.raises(HarnessSafetyError):
        recovery.open_recovery_readonly(READONLY_USER, '')
    connect.assert_not_called()


def test_open_connects_to_fixed_target_with_the_dedicated_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep the read-only session on the fixed local target without init SQL or write-capable defaults.
    @param monkeypatch: Driver spy.
    @return: None.
    """
    monkeypatch.setenv('TEST_DB_SERVER_UUID', '11111111-2222-4333-8444-555555555555')
    connect = MagicMock()
    monkeypatch.setattr(recovery.pymysql, 'connect', connect)
    recovery.open_recovery_readonly(READONLY_USER, 'synthetic-private')
    arguments = connect.call_args.kwargs
    assert (arguments['host'], arguments['port'], arguments['user'], arguments['database']) == (
        '127.0.0.1', 3306, READONLY_USER, 'jukebox101_test')
    assert arguments['password'] == 'synthetic-private' and 'init_command' not in arguments


def test_main_refuses_a_header_without_account_before_reading_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Require the explicit account in the header so no default identity is ever chosen.
    @param monkeypatch: Spies for driver and journal.
    @return: None.
    """
    scope = RunScope()
    monkeypatch.setattr(recovery, 'read_journal', lambda _: (journal_fixture(scope), 'd' * 64, scope))
    open_readonly = MagicMock()
    monkeypatch.setattr(recovery, 'open_recovery_readonly', open_readonly)
    stream = io.StringIO(json.dumps(dict(authorization='BATCH_B_RECOVERY_READONLY', journal='synthetic')) + '\n"synthetic-private"\n')
    assert recovery.main(stream) == 1
    open_readonly.assert_not_called()


def test_main_refuses_the_harness_account_even_when_named_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Refuse the harness name in the header because the read-only path must not fall back to it.
    @param monkeypatch: Spies for driver and journal.
    @return: None.
    """
    scope = RunScope()
    monkeypatch.setattr(recovery, 'read_journal', lambda _: (journal_fixture(scope), 'd' * 64, scope))
    open_harness, open_readonly = MagicMock(), MagicMock()
    monkeypatch.setattr(recovery, 'open_read_only', open_harness)
    monkeypatch.setattr(recovery, 'open_recovery_readonly', open_readonly)
    header = dict(authorization='BATCH_B_RECOVERY_READONLY', journal='synthetic', account=HARNESS_USER)
    stream = io.StringIO(json.dumps(header) + '\n"synthetic-private"\n')
    assert recovery.main(stream) == 1
    open_harness.assert_not_called()
    open_readonly.assert_not_called()


def test_main_uses_only_the_dedicated_account(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """@description: Dispatch the inspect through the read-only opener and never through the harness opener.
    @param monkeypatch: Spies for driver, journal and inspection.
    @param capsys: Captured sanitized report.
    @return: None.
    """
    scope = RunScope()
    digest = hashlib.sha256(b'synthetic').hexdigest()
    monkeypatch.setattr(recovery, 'read_journal', lambda _: (journal_fixture(scope), digest, scope))
    monkeypatch.setattr(recovery.Path, 'read_bytes', lambda _: b'synthetic')
    connection = MagicMock()
    open_harness, open_readonly = MagicMock(), MagicMock(return_value=connection)
    monkeypatch.setattr(recovery, 'open_read_only', open_harness)
    monkeypatch.setattr(recovery, 'open_recovery_readonly', open_readonly)
    monkeypatch.setattr(recovery, 'inspect_connection', lambda *args: dict(recovery_writes_authorized=False))
    header = dict(authorization='BATCH_B_RECOVERY_READONLY', journal='synthetic', account=READONLY_USER)
    stream = io.StringIO(json.dumps(header) + '\n"synthetic-private"\n')
    assert recovery.main(stream) == 0
    open_readonly.assert_called_once_with(READONLY_USER, 'synthetic-private')
    open_harness.assert_not_called()
    assert 'synthetic-private' not in capsys.readouterr().out


def test_grants_accept_exactly_usage_and_twenty_three_selects() -> None:
    """@description: Accept the reviewed read-only grant set and no other effective privilege.
    @param: none
    @return: None.
    """
    recovery.verify_readonly_grants(GrantCursor(exact_grants()))


@pytest.mark.parametrize('grants,role', [
    (exact_grants() + ['GRANT INSERT ON `jukebox101_test`.`users` TO `' + READONLY_USER + '`@`127.0.0.1`'], 'NONE'),
    (exact_grants() + ['GRANT SELECT ON *.* TO `' + READONLY_USER + '`@`127.0.0.1`'], 'NONE'),
    (exact_grants() + ['GRANT SELECT ON `jukebox101_test`.* TO `' + READONLY_USER + '`@`127.0.0.1`'], 'NONE'),
    (exact_grants()[:-1] + [exact_grants()[-1] + ' WITH GRANT OPTION'], 'NONE'),
    (exact_grants()[:-1], 'NONE'),
    (exact_grants(), 'some_role'),
])
def test_grants_reject_extra_or_missing_authority(grants: list, role: str) -> None:
    """@description: Reject any extra privilege, grant option, schema-wide grant, missing table or active role.
    @param grants: Synthetic SHOW GRANTS lines.
    @param role: Synthetic CURRENT_ROLE() value.
    @return: None.
    """
    with pytest.raises(HarnessSafetyError):
        recovery.verify_readonly_grants(GrantCursor(grants, role))


def test_inspect_checks_the_readonly_identity_and_grants_not_the_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Prove the account name is only an input: the inspect checks identity and grants for the read-only account before reading.
    @param monkeypatch: Spies for identity and grant checks.
    @return: None.
    """
    identity, harness_grants = MagicMock(), MagicMock()
    monkeypatch.setattr(recovery, 'check_identity', identity)
    monkeypatch.setattr(recovery, 'verify_harness_grants', harness_grants)
    monkeypatch.setattr(recovery, 'verify_readonly_grants', MagicMock(side_effect=HarnessSafetyError('READONLY_GRANTS_REJECTED')))
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value = GrantCursor(exact_grants())
    with pytest.raises(HarnessSafetyError):
        recovery.inspect_connection(connection, journal_fixture(RunScope()), RunScope())
    identity.assert_called_once()
    assert identity.call_args.args[1] == READONLY_USER
    harness_grants.assert_not_called()
