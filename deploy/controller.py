"""Execute a stop-before-start transaction over a trusted backend and durable journal.

The shipped backend is rehearsal-only; authorization defaults to refusal. No
database restore, migration, shell command or arbitrary Compose path is accepted.
"""

import re
from pathlib import Path

from deploy.journal import MAX_REQUESTS, exclusive, load, persist

REQUEST_PATTERN = re.compile(r'deploy-v1 ([a-f0-9]{40}) (sha256:[a-f0-9]{64}) ([1-9][0-9]{0,19}) ([1-9][0-9]{0,5})\n?')
TERMINAL = {'succeeded', 'rolled_back', 'rollback_failed', 'rejected'}


def parse_request(line: str) -> dict:
    """@description: Accept exactly one bounded deployment request without shell parsing.
    @param line: Single SSH stdin request, maximum 256 ASCII bytes.
    @return: Validated request fields; malformed input raises ValueError.
    """
    matched = REQUEST_PATTERN.fullmatch(line) if len(line) <= 256 else None
    if matched is None:
        raise ValueError('request format')
    commit, digest, run, attempt = matched.groups()
    return {'commit': commit, 'digest': digest, 'run': int(run), 'attempt': int(attempt)}


def deny_unverified(request: dict) -> bool:
    """@description: Fail closed until an independent receipt verifier is implemented.
    @param request: Request whose self-reported provenance establishes no trust.
    @return: Always False; no env flag or sentinel overrides this missing verifier.
    """
    return False


class Controller:
    """Own serialized transactions; a backend supplies bounded lifecycle operations."""

    def __init__(self, directory: Path, backend, authorize=deny_unverified):
        """@description: Bind a trusted backend, journal and independent authorization boundary.
        @param self: Deployment controller.
        @param directory: Administrator-owned state or private rehearsal directory.
        @param backend: Lifecycle adapter, never selected by a deployment caller.
        @param authorize: Independent source/receipt policy; default denies all requests.
        @return: None.
        """
        self.directory = directory
        self.path = directory / 'journal.json'
        self.backend = backend
        self.authorize = authorize
        self.document = None  # [durable state]: Loaded only while holding the controller lock.

    def _save(self, transaction: dict, phase: str, status: str = 'running') -> None:
        """@description: Persist intent before every side effect and outcome afterwards.
        @param self: Locked controller.
        @param transaction: Active request's durable record.
        @param phase: Next recovery-relevant phase.
        @param status: Public request result.
        @return: None.
        """
        transaction.update(phase=phase, status=status)
        persist(self.path, self.document)

    def execute(self, request: dict) -> str:
        """@description: Reconcile interrupted work before admitting another request.
        @param self: Controller with trusted collaborators.
        @param request: Parsed request; the SSH adapter must not deserialize arbitrary objects.
        @return: Terminal status, or rejection/busy; rollback success remains deployment failure.
        """
        with exclusive(self.directory):
            self.document = load(self.path)
            self._recover()
            key = f"{request['run']}-{request['attempt']}"
            existing = self.document['requests'].get(key)
            if existing is not None:
                return existing['status'] if existing['request'] == request else 'rejected'
            if any(item['status'] == 'rollback_failed' for item in self.document['requests'].values()):
                return 'rejected'
            if not self._admissible(request):
                return 'rejected'
            transaction = {'request': request, 'previous': self.document['last_known_good']}
            self.document['requests'][key] = transaction
            self.document['highwater'] = max(self.document['highwater'], request['run'])
            self._save(transaction, 'prepared')
            return self._deploy(transaction)

    def _admissible(self, request: dict) -> bool:
        """@description: Reject unverified, stale or over-capacity requests before stopping anything.
        @param self: Locked controller.
        @param request: Candidate release request.
        @return: Whether all admission gates permit preflight.
        """
        return (len(self.document['requests']) < MAX_REQUESTS and
                request['run'] > self.document['highwater'] and self.authorize(request))

    def _stop(self, transaction: dict, phase: str) -> None:
        """@description: Confirm old process exit before any candidate or rollback start.
        @param self: Locked controller.
        @param transaction: Durable transaction being reconciled.
        @param phase: Journal intent used if the controller disappears during stop.
        @return: None; ambiguous exit stops progress.
        """
        self._save(transaction, phase)
        self.backend.stop()
        if not self.backend.is_stopped():
            raise RuntimeError('gateway_stop_unconfirmed')

    def _deploy(self, transaction: dict) -> str:
        """@description: Test candidate health before changing the last-known-good release.
        @param self: Locked controller.
        @param transaction: Journal entry preserving the previous compatible release.
        @return: Truthful terminal deployment result.
        """
        candidate = transaction['request']['digest']
        try:
            self.backend.preflight(candidate, transaction['previous'])
            self.backend.pull(candidate)
        except Exception as error:
            transaction['error'] = type(error).__name__
            self._save(transaction, 'preflight_rejected', 'rejected')
            return 'rejected'
        try:
            self._stop(transaction, 'stopping_old')
            self._save(transaction, 'starting_candidate')
            self.backend.start(candidate)
            self._save(transaction, 'observing_candidate')
            if not self.backend.healthy(candidate):
                raise RuntimeError('candidate_unhealthy')
            self.document['last_known_good'] = candidate
            self._save(transaction, 'complete', 'succeeded')
            return 'succeeded'
        except Exception as error:
            transaction['error'] = type(error).__name__
            return self._rollback(transaction)

    def _rollback(self, transaction: dict) -> str:
        """@description: Attempt exactly one recovery, keeping database state untouched.
        @param self: Locked controller.
        @param transaction: Durable record identifying the prior compatible release.
        @return: rolled_back or rollback_failed, never success for a failed candidate.
        """
        self.document['last_known_good'] = transaction['previous']
        try:
            self._stop(transaction, 'rollback_stopping')
            self._save(transaction, 'rollback_starting')
            self.backend.start(transaction['previous'])
            self._save(transaction, 'rollback_observing')
            if not self.backend.healthy(transaction['previous']):
                raise RuntimeError('rollback_unhealthy')
            self._save(transaction, 'complete', 'rolled_back')
            return 'rolled_back'
        except Exception as error:
            transaction['rollback_error'] = type(error).__name__
            self._save(transaction, 'manual_intervention', 'rollback_failed')
            return 'rollback_failed'

    def _recover(self) -> None:
        """@description: Roll back an interrupted candidate once before accepting new work.
        @param self: Controller holding the exclusive lock with a loaded journal.
        @return: None; interrupted rollback is latched for manual recovery, never retried endlessly.
        """
        for transaction in self.document['requests'].values():
            if transaction['status'] in TERMINAL:
                continue
            if transaction['phase'] == 'prepared':
                self._save(transaction, 'interrupted_before_stop', 'rejected')
            elif transaction['phase'].startswith('rollback_'):
                self._save(transaction, 'interrupted_rollback', 'rollback_failed')
            else:
                self._rollback(transaction)
