"""Describe one exact temporary self-FK change for disposable cleanup.

Shares schema-delta checks and guarded DDL between recovery and future suite supervision without granting DDL to the harness.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from tests.batch_b_harness_contract import HarnessSafetyError, TABLES

ROOT_FK = re.compile(r'^  CONSTRAINT `fk_jb2_version_root` FOREIGN KEY \(`canonical_id`\) REFERENCES `jb2_versions` \(`version_id`\)'
                     r'(?: ON DELETE (?:NO ACTION|RESTRICT|CASCADE))?(?: ON UPDATE (?:NO ACTION|RESTRICT))?(,?)$', re.MULTILINE)
TEMP_ROOT_NAME = 'fk_jb2_version_root_recovery'  # [reserved temporary identity]: Never reuse the still-existing original FK name.
TEMP_ROOT_FK = re.compile(ROOT_FK.pattern.replace('fk_jb2_version_root`', TEMP_ROOT_NAME + '`'), re.MULTILINE)


@dataclass(frozen=True)
class FkWindow:
    """Keep immutable original and expected temporary definitions; callers cannot approve an arbitrary schema delta."""

    original: tuple[tuple[str, str], ...]  # [restore authority]: Exact approved metadata, not current unreviewed state.
    cascade: tuple[tuple[str, str], ...]  # [temporary scope]: Only the root DELETE action may differ.
    original_clause: str  # [exact FK restoration]: Preserve original explicit and omitted action syntax.
    cascade_clause: str  # [fixed temporary DDL]: Parsed from the approved original clause.
    bridge_original_first: tuple[tuple[str, str], ...]  # [dual enforcement]: Both reviewed FKs exist between DDL steps.
    bridge_temporary_first: tuple[tuple[str, str], ...]  # [ordering allowance]: Permit only the two FK enumeration orders.


def digest(definitions: dict) -> str:
    """@description: Reuse the recovery schema fingerprint algorithm for both DDL boundaries.
    @param definitions: Normalized complete SHOW CREATE inventory.
    @return: SHA256 excluding no new structural information.
    """
    return hashlib.sha256(json.dumps(definitions, sort_keys=True).encode()).hexdigest()


def build_window(definitions: dict, approved: str) -> FkWindow:
    """@description: Derive the only permitted CASCADE delta from a previously approved original schema.
    @param definitions: Complete original schema, never a caller-authored replacement clause.
    @param approved: Independently approved original fingerprint.
    @return: Immutable exact original and temporary states.
    """
    if set(definitions) != TABLES or digest(definitions) != approved:
        raise HarnessSafetyError('FK_WINDOW_ORIGINAL_SCHEMA_REJECTED')
    matches = tuple(ROOT_FK.finditer(definitions['jb2_versions']))  # [one allowed structural delta]: All other bytes must stay identical.
    if len(matches) != 1 or 'ON DELETE CASCADE' in matches[0][0]:
        raise HarnessSafetyError('FK_WINDOW_ORIGINAL_CONSTRAINT_REJECTED')
    original = matches[0][0].strip().rstrip(',')  # [restore clause]: Trusted metadata restricted by the exact root pattern.
    clause = re.sub(r' ON DELETE (NO ACTION|RESTRICT)', '', original)
    position = clause.find(' ON UPDATE ')
    cascade = clause + ' ON DELETE CASCADE' if position == -1 else clause[:position] + ' ON DELETE CASCADE' + clause[position:]
    cascade = cascade.replace('`fk_jb2_version_root`', '`' + TEMP_ROOT_NAME + '`', 1)
    changed = replace_clauses(definitions, matches[0], (cascade,))  # [expected post-DDL structure]: Only reviewed FK clauses may differ.
    bridge = replace_clauses(definitions, matches[0], (original, cascade))  # [continuous enforcement]: Both FKs exist before either is removed.
    reverse = replace_clauses(definitions, matches[0], (cascade, original))  # [permitted presentation order]: No other normalization is allowed.
    return FkWindow(tuple(sorted(definitions.items())), tuple(sorted(changed.items())), original, cascade,
                    tuple(sorted(bridge.items())), tuple(sorted(reverse.items())))


def replace_clauses(definitions: dict, match: re.Match, clauses: tuple[str, ...]) -> dict:
    """@description: Derive exact bridge variants without normalizing away unrelated schema drift.
    @param definitions: Approved original SHOW CREATE inventory.
    @param match: Unique original root constraint match.
    @param clauses: One or two fixed parsed clauses, never user input.
    @return: Full expected definitions preserving every other byte and comma.
    """
    changed = dict(definitions)
    replacement = ',\n'.join('  ' + clause for clause in clauses) + match[1]
    changed['jb2_versions'] = definitions['jb2_versions'][:match.start()] + replacement + definitions['jb2_versions'][match.end():]
    return changed


def require_schema(definitions: dict, window: FkWindow, cascade: bool | str) -> None:
    """@description: Forbid every structural change except the precomputed single-FK action switch.
    @param definitions: Fresh complete metadata with auto-increment counters normalized by existing code.
    @param window: Immutable reviewed state pair.
    @param cascade: Original False, temporary True or strictly enumerated dual-FK bridge.
    @return: None; drift never authorizes a corrective DDL automatically.
    """
    variants = (window.bridge_original_first, window.bridge_temporary_first) if cascade == 'bridge' else (window.cascade if cascade is True else window.original,)
    if cascade not in (True, False, 'bridge') or not any(definitions == dict(expected) for expected in variants):
        raise HarnessSafetyError('FK_WINDOW_SCHEMA_DRIFT')


def constraint_names(original: set, phase: bool | str) -> set:
    """@description: Permit only the exact original, temporary or overlapping root constraint names.
    @param original: Reviewed full original FK name set.
    @param phase: Original False, temporary True or dual-FK bridge.
    @return: Complete expected name set, never arbitrary caller-supplied additions.
    """
    if phase not in (True, False, 'bridge'):
        raise HarnessSafetyError('FK_WINDOW_PHASE_REJECTED')
    expected = set(original)  # [exact FK inventory]: Other constraints are never renamed or omitted.
    if phase is True:
        expected.remove(('jb2_versions', 'fk_jb2_version_root'))
    if phase is True or phase == 'bridge':
        expected.add(('jb2_versions', TEMP_ROOT_NAME))
    return expected


def switch_sql(window: FkWindow, cascade: bool) -> tuple[str, str]:
    """@description: Avoid same-name 1826 and unsupported COPY add/drop combinations while retaining an enforcing FK.
    @param window: Prevalidated original and temporary clauses.
    @param cascade: Install CASCADE or restore the exact original clause.
    @return: ADD the absent alternative first, then DROP the prior name, as separate reviewed statements.
    """
    clause = window.cascade_clause if cascade else window.original_clause
    previous = 'fk_jb2_version_root' if cascade else TEMP_ROOT_NAME
    target = 'ALTER TABLE `jukebox101_test`.`jb2_versions` '
    return target + 'ADD ' + clause, target + 'DROP FOREIGN KEY `' + previous + '`'


def apply_switch(connection: Any, window: FkWindow, cascade: bool, state: dict, persist: Any, checkpoint: Any) -> None:
    """@description: Validate original, dual-FK and final states around two separately journaled DDL attempts.
    @param connection: Existing DBA connection after full exclusive preflight and transaction release.
    @param window: Reviewed schema pair, never arbitrary SQL.
    @param cascade: Requested switch direction.
    @param state: Sanitized attempt outcome; UNKNOWN requires read-only investigation.
    @param persist: Existing durable journal callback, called before any DDL.
    @param checkpoint: Full fresh schema, data, privilege and exclusivity boundary validator.
    @return: None; this helper never retries or claims rollback can undo ALTER.
    """
    label = 'cascade_ddl_outcome' if cascade else 'restore_ddl_outcome'  # [direction outcome]: Never infer it from only the first DDL.
    state[label] = 'PENDING'
    state['fk_restoration_pending'] = True
    statements = switch_sql(window, cascade)  # [non-overlapping names]: The original and alternative are never both absent.
    try:
        checkpoint(not cascade)
        for index, statement in enumerate(statements):
            execute_step(connection, statement, label, index, state, persist)
            phase = 'bridge' if index == 0 else cascade
            checkpoint(phase)
            state['fk_verified_state'] = 'BRIDGE' if phase == 'bridge' else 'CASCADE' if phase else 'ORIGINAL'
            persist(state, 'R_FK_DDL_CHECKPOINT_VERIFIED')
    except BaseException:
        state[label] = 'UNKNOWN'
        raise
    state[label] = 'ACKNOWLEDGED'
    persist(state, 'R_FK_DDL_ACKNOWLEDGED')


def execute_step(connection: Any, sql: str, direction: str, index: int, state: dict, persist: Any) -> None:
    """@description: Retain exact reviewed DDL and its independent outcome before any next step is permitted.
    @param connection: Verified existing DBA connection.
    @param sql: Fixed helper-generated statement, without credentials or user values.
    @param direction: Reviewed switch direction label.
    @param index: Zero-based ADD then DROP position.
    @param state: Safe new attempt journal with no mutation of previous journals.
    @param persist: Existing atomic diagnostic writer.
    @return: None; UNKNOWN forbids moving to the next DDL or retrying this one.
    """
    progress = dict(direction=direction, step=index + 1, sql=sql, outcome='PENDING')  # [DDL audit]: Separate from manifest DELETE parameters.
    state.setdefault('fk_ddl_progress', []).append(progress)
    persist(state, 'R_FK_DDL_ATTEMPT')
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
    except BaseException:
        progress['outcome'] = 'UNKNOWN'
        raise
    progress['outcome'] = 'ACKNOWLEDGED'
    persist(state, 'R_FK_DDL_STEP_ACKNOWLEDGED')


def require_no_version_children(cursor: Any, version_id: str) -> None:
    """@description: Prevent CASCADE from silently deleting aliases outside individual PK deletion evidence.
    @param cursor: Serializable cleanup cursor after full run ownership verification.
    @param version_id: Exact next version PK, whose registered children must already be deleted.
    @return: None; even a registered leftover child aborts cleanup.
    """
    cursor.execute('SELECT version_id FROM jb2_versions WHERE canonical_id=%s AND version_id<>%s', (version_id, version_id))
    if cursor.fetchall():
        raise HarnessSafetyError('FK_WINDOW_ALIAS_STILL_PRESENT')


def require_cleanup_cascade(definitions: dict) -> None:
    """@description: Stop normal testing before enabling writes when self-root cleanup is known to fail.
    @param definitions: Exact suite baseline inside a separately DBA-supervised FK window.
    @return: None; this guard never changes any schema or obtains DBA credentials.
    """
    source = definitions.get('jb2_versions', '')
    matches = tuple(TEMP_ROOT_FK.finditer(source))
    if len(matches) != 1 or 'ON DELETE CASCADE' not in matches[0][0] or ROOT_FK.search(source):
        raise HarnessSafetyError('SELF_ROOT_CLEANUP_REQUIRES_FK_WINDOW')
