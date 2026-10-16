"""Run three isolated InnoDB experiments only after separate operator authorization.

Uses the existing anonymous-stdin credential bridge and never selects a JukeBox101 business schema.
"""

import json
import re
from typing import Any, TextIO
from uuid import UUID
import pymysql
from tests.batch_b_acceptance_setup import query, record_error, full_metadata_authority
from tests.batch_b_harness_contract import HarnessSafetyError

SCHEMA = 'jukebox101_fk_lab'
CASES = ('no_action', 'cascade_root', 'cascade_alias')
# [equivalent global DBA authority]: MySQL 8.0 SHOW GRANTS expands ALL into these static privileges.
MYSQL80_STATIC_ALL = frozenset(('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'RELOAD', 'SHUTDOWN',
    'PROCESS', 'FILE', 'REFERENCES', 'INDEX', 'ALTER', 'SHOW DATABASES', 'SUPER', 'CREATE TEMPORARY TABLES',
    'LOCK TABLES', 'EXECUTE', 'REPLICATION SLAVE', 'REPLICATION CLIENT', 'CREATE VIEW', 'SHOW VIEW',
    'CREATE ROUTINE', 'ALTER ROUTINE', 'CREATE USER', 'EVENT', 'TRIGGER', 'CREATE TABLESPACE', 'CREATE ROLE', 'DROP ROLE'))


def existing_dba_authority(grants: tuple[dict, ...]) -> bool:
    """@description: Recognize equivalent explicit global authority without accepting unknown or partial grants.
    @param grants: Private own-account SHOW GRANTS rows, never authentication information.
    @return: Whether an existing global DBA grant is complete and has no visible partial revokes.
    """
    statements = tuple(next(iter(row.values())).replace('`', '') for row in grants)  # [private ACL evidence]: Do not log raw SQL.
    if any(statement.startswith('REVOKE ') for statement in statements):
        return False
    for statement in statements:
        if not full_metadata_authority(statement):
            continue
        privileges = {value.strip() for value in statement.removeprefix('GRANT ').split(' ON *.* TO ', 1)[0].split(',')}
        if privileges == {'ALL PRIVILEGES'} or MYSQL80_STATIC_ALL.issubset(privileges):
            return True
    return False


def receive(stream: TextIO) -> tuple[dict, str]:
    """@description: Require explicit lab-only authorization before any network access.
    @param stream: Private header and one masked DBA credential from the existing launcher.
    @return: Validated header and short-lived password, never serialized.
    """
    header = json.loads(stream.readline(4096))  # [lab authority]: Ambient test switches cannot authorize DDL.
    if set(header) != {'authorization', 'dba_user', 'server_uuid'} or header['authorization'] != 'FK_LAB_CREATE_AND_TEST':
        raise HarnessSafetyError('FK_LAB_AUTHORIZATION_REQUIRED')
    if not isinstance(header['dba_user'], str) or not re.fullmatch('[A-Za-z0-9_]{1,32}', header['dba_user']):
        raise HarnessSafetyError('FK_LAB_USER_REJECTED')
    if header['dba_user'].startswith('jukebox101_'):
        raise HarnessSafetyError('FK_LAB_DBA_REQUIRED')
    if str(UUID(header['server_uuid'])) != header['server_uuid'].lower():
        raise HarnessSafetyError('FK_LAB_UUID_REJECTED')
    password = json.loads(stream.readline(16384))  # [private credential]: Only process memory and anonymous stdin.
    if not isinstance(password, str) or not password or len(password) > 4096 or '\x00' in password:
        raise HarnessSafetyError('FK_LAB_CREDENTIAL_REJECTED')
    return header, password


def table_name(case: str) -> str:
    """@description: Restrict all experiment writes to three static fully qualified identifiers.
    @param case: Reviewed experiment name, never arbitrary caller SQL.
    @return: Lab-only SQL identifier.
    """
    if case not in CASES:
        raise HarnessSafetyError('FK_LAB_CASE_REJECTED')
    return '`jukebox101_fk_lab`.`probe_' + case + '`'


def verify_server(cursor: Any, header: dict) -> None:
    """@description: Pin the local server and full metadata visibility before creating the isolated schema.
    @param cursor: DBA cursor connected without a default database.
    @param header: Previously approved UUID and local DBA identity.
    @return: None; mismatch stops before DDL.
    """
    row = query(cursor, 'SELECT DATABASE() db,CURRENT_USER() account,@@port port,@@server_uuid uuid,VERSION() version,@@foreign_key_checks fk')[0]
    if (row['db'], row['port'], row['uuid'].lower(), row['fk']) != (None, 3306, header['server_uuid'].lower(), 1):
        raise HarnessSafetyError('FK_LAB_SERVER_REJECTED')
    if row['account'] not in {header['dba_user'] + '@localhost', header['dba_user'] + '@127.0.0.1'} or not row['version'].startswith('8.0.'):
        raise HarnessSafetyError('FK_LAB_IDENTITY_REJECTED')
    grants = query(cursor, 'SHOW GRANTS FOR CURRENT_USER()')  # [existence visibility]: Empty metadata is insufficient for a restricted user.
    if not existing_dba_authority(grants):
        raise HarnessSafetyError('FK_LAB_EXISTING_DBA_REQUIRED')
    if query(cursor, 'SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (SCHEMA,)):
        raise HarnessSafetyError('FK_LAB_ALREADY_EXISTS')
    if query(cursor, "SELECT GET_LOCK('jukebox101_fk_lab_experiment',0) owned")[0]['owned'] != 1:
        raise HarnessSafetyError('FK_LAB_BUSY')


def create_lab(cursor: Any) -> None:
    """@description: Create only a previously absent lab and three minimal InnoDB tables.
    @param cursor: Verified local DBA session holding the lab advisory lock.
    @return: None; DDL is not presented as transactional or automatically retried.
    """
    cursor.execute('CREATE DATABASE `jukebox101_fk_lab`')
    for case in CASES:
        action = 'NO ACTION' if case == 'no_action' else 'CASCADE'  # [controlled variable]: Only delete action differs.
        cursor.execute('CREATE TABLE ' + table_name(case) + ' (version_id VARCHAR(128) PRIMARY KEY,canonical_id VARCHAR(128) NOT NULL,'
                       'CONSTRAINT `probe_' + case + '_root` FOREIGN KEY (canonical_id) REFERENCES ' + table_name(case) +
                       ' (version_id) ON DELETE ' + action + ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin')


def experiment(connection: Any, cursor: Any, case: str) -> dict:
    """@description: Measure actual server behavior and roll back every experiment's data.
    @param connection: Owned lab connection; business repositories are never called.
    @param cursor: Owned cursor restricted to static lab SQL by the executor.
    @param case: One of the three reviewed experiment names.
    @return: Observed error number, remaining count and acknowledged rollback.
    """
    connection.begin()
    try:
        cursor.execute('INSERT INTO ' + table_name(case) + ' VALUES (%s,%s)', ('root', 'root'))
        if case == 'cascade_alias':
            cursor.execute('INSERT INTO ' + table_name(case) + ' VALUES (%s,%s)', ('alias', 'root'))
        number = delete_root(cursor, case)  # [observed evidence]: A mock result is never a real InnoDB verification.
        remaining = query(cursor, 'SELECT COUNT(*) count FROM ' + table_name(case))[0]['count']
    finally:
        connection.rollback()
    expected = (1451, 1) if case == 'no_action' else (None, 0)
    return dict(case=case, mysql_error_number=number, remaining=remaining, passed=(number, remaining) == expected, rollback='ACKNOWLEDGED')


def delete_root(cursor: Any, case: str) -> int | None:
    """@description: Preserve expected FK refusal without exposing raw driver text.
    @param cursor: Lab-only experiment cursor.
    @param case: Reviewed table case.
    @return: 1451 on observed FK restriction, or None on successful DELETE.
    """
    try:
        cursor.execute('DELETE FROM ' + table_name(case) + ' WHERE version_id=%s', ('root',))
    except pymysql.IntegrityError as error:
        if error.args and error.args[0] == 1451:
            return 1451
        raise
    return None


def cleanup_lab(cursor: Any) -> None:
    """@description: Drop only the three created tables after proving rollback left every table empty.
    @param cursor: Same exclusively owned lab session after successful experiment rollbacks.
    @return: None; an unexpected object or residual row preserves the lab for investigation.
    """
    tables = query(cursor, 'SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s', (SCHEMA,))
    if {row['TABLE_NAME'] for row in tables} != {'probe_' + case for case in CASES}:
        raise HarnessSafetyError('FK_LAB_OBJECT_DRIFT')
    if any(query(cursor, 'SELECT COUNT(*) count FROM ' + table_name(case))[0]['count'] for case in CASES):
        raise HarnessSafetyError('FK_LAB_ROLLBACK_RESIDUE')
    for case in CASES:
        cursor.execute('DROP TABLE ' + table_name(case))
    if query(cursor, 'SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s', (SCHEMA,)):
        raise HarnessSafetyError('FK_LAB_CLEANUP_INCOMPLETE')


def execute(header: dict, password: str, state: dict) -> None:
    """@description: Own one lab connection without invoking acceptance, recovery or credential stores.
    @param header: Explicit separately authorized lab protocol.
    @param password: Private existing DBA credential.
    @param state: Sanitized progress and actual experiment results.
    @return: None; failure leaves objects for inspection and never retries DDL.
    """
    state['current_step'] = 'FK_LAB_CONNECT'
    connection = pymysql.connect(host='127.0.0.1', port=3306, user=header['dba_user'], password=password,
                                 autocommit=False, connect_timeout=5, read_timeout=10, write_timeout=10, cursorclass=pymysql.cursors.DictCursor)
    try:
        with connection.cursor() as cursor:
            state['current_step'] = 'FK_LAB_PREFLIGHT'
            verify_server(cursor, header)
            cursor.execute('SET SESSION innodb_lock_wait_timeout=2')
            state['current_step'] = 'FK_LAB_DDL'
            create_lab(cursor)
            state['tests'] = []  # [actual measurements]: Completion is counted only after rollback acknowledgement.
            state['current_step'] = 'FK_LAB_EXPERIMENTS'
            for case in CASES:
                state['tests'].append(experiment(connection, cursor, case))
            if not all(item['passed'] for item in state['tests']):
                raise HarnessSafetyError('FK_LAB_UNEXPECTED_BEHAVIOR')
            state['current_step'] = 'FK_LAB_CLEANUP'
            cleanup_lab(cursor)
            state.update(stage='FK_LAB_COMPLETE', lab_cleaned=True)
    finally:
        connection.close()


def main(stream: TextIO) -> int:
    """@description: Emit sanitized lab evidence without authorizing recovery or changing its blocked state.
    @param stream: Existing launcher's private input pipe.
    @return: Zero only after all real experiments and lab-table cleanup succeed.
    """
    state = dict(stage='FK_LAB_FAILED', session_blocked=True, lab_cleaned=False)  # [lab outcome]: Never means business baseline restored.
    password = None
    try:
        header, password = receive(stream)
        execute(header, password, state)
        return 0
    except BaseException as error:
        record_error(state, error)
        return 1
    finally:
        password = None
        print(json.dumps(state, ensure_ascii=True))
