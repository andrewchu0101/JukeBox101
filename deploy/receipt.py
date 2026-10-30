"""Verify release provenance with the official GitHub CLI and host-owned policy.

This adapter never implements cryptography itself. Tests exercise policy with fake
CLI output; real signature verification remains a separate required acceptance gate.
"""

import json
import subprocess

from deploy.controller import parse_request

REPOSITORY = 'andrewchu0101/JukeBox101'
REPOSITORY_ID = 1430572666
IMAGE = 'ghcr.io/andrewchu0101/jukebox101'
WORKFLOW = '.github/workflows/release.yml'
SOURCE_REF = 'refs/heads/main'
PREDICATE = 'https://slsa.dev/provenance/v1'
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ReceiptRefused(RuntimeError):
    """Report a stable refusal category without echoing CLI output or credentials."""


class GithubReceiptVerifier:
    """Bind cryptographic verification to exact source, workflow, run and host policy."""

    def __init__(self, policy: dict, runner=subprocess.run):
        """@description: Accept policy only from a trusted root-owned loader, never a deployment request.
        @param self: Release verifier.
        @param policy: Fixed host allowlist and explicit acceptance gates.
        @param runner: Subprocess boundary; fake only in unit tests.
        @return: None.
        """
        self.policy = policy
        self.runner = runner

    def command(self, arguments: list[str]) -> object:
        """@description: Run only the administrator-selected CLI with a fixed environment and timeout.
        @param self: Release verifier.
        @param arguments: Trusted CLI argv, never a shell expression.
        @return: Bounded decoded JSON; errors fail closed without leaking stdout/stderr.
        """
        environment = {'PATH': '/usr/bin:/bin', 'HOME': '/var/lib/jukebox101-cd',
                       'GH_HOST': 'github.com', 'GH_PROMPT_DISABLED': '1',
                       'GH_CONFIG_DIR': '/etc/jukebox101/gh'}
        result = self.runner(['/usr/bin/gh', *arguments], env=environment, text=True,
                             capture_output=True, timeout=60, cwd='/')
        if result.returncode or len(result.stdout.encode()) > MAX_RESPONSE_BYTES:
            raise ReceiptRefused('VERIFIER_COMMAND_FAILED')
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError) as error:
            raise ReceiptRefused('VERIFIER_OUTPUT_INVALID') from error

    def verify(self, request: dict) -> bool:
        """@description: Require independent source and signature evidence before any Docker operation.
        @param self: Verifier whose acceptance flag defaults to disabled.
        @param request: Strictly validated deployment identity; its claims establish no trust.
        @return: True only after every gate passes; otherwise raises ReceiptRefused.
        """
        parse_request(f"deploy-v1 {request['commit']} {request['digest']} {request['run']} {request['attempt']}")
        if self.policy.get('enabled') is not True or self.policy.get('crypto_acceptance_verified') is not True:
            raise ReceiptRefused('CD_DISABLED_OR_UNVERIFIED')
        self._verify_current_main(request)
        self._verify_run(request)
        self._verify_tree(request)
        self._verify_signatures(request)
        return True

    def _verify_current_main(self, request: dict) -> None:
        """@description: Reject old main commits and repository-name reuse.
        @param self: Verifier.
        @param request: Candidate source commit.
        @return: None.
        """
        repository = self.command(['api', f'repos/{REPOSITORY}'])
        branch = self.command(['api', f'repos/{REPOSITORY}/branches/main'])
        if repository.get('id') != REPOSITORY_ID or branch['commit']['sha'] != request['commit']:
            raise ReceiptRefused('SOURCE_NOT_CURRENT_MAIN')

    def _verify_run(self, request: dict) -> None:
        """@description: Require the exact completed successful main push attempt and workflow.
        @param self: Verifier.
        @param request: Candidate run/attempt identity.
        @return: None.
        """
        prefix = f"repos/{REPOSITORY}/actions/runs/{request['run']}/attempts/{request['attempt']}"
        run = self.command(['api', prefix])
        expected = {'id': request['run'], 'run_attempt': request['attempt'], 'head_sha': request['commit'],
                    'head_branch': 'main', 'event': 'push', 'status': 'completed', 'conclusion': 'success',
                    'path': WORKFLOW}
        if any(run.get(key) != value for key, value in expected.items()):
            raise ReceiptRefused('RUN_NOT_SUCCESSFUL_RELEASE')
        if run.get('repository', {}).get('id') != REPOSITORY_ID:
            raise ReceiptRefused('RUN_REPOSITORY_MISMATCH')

    def _verify_tree(self, request: dict) -> None:
        """@description: Require reviewed workflow/schema blobs, rejecting implicit migration changes.
        @param self: Verifier.
        @param request: Exact main source commit.
        @return: None.
        """
        allowed = self.policy.get('approved_blobs', {})
        required = {WORKFLOW, '.github/workflows/ci.yml'}
        if not required <= set(allowed) or not any(path.startswith('bot/database/postgres/') for path in allowed):
            raise ReceiptRefused('APPROVED_POLICY_INCOMPLETE')
        tree = self.command(['api', f"repos/{REPOSITORY}/git/trees/{request['commit']}?recursive=1"])
        if tree.get('truncated') is not False:
            raise ReceiptRefused('SOURCE_TREE_INCOMPLETE')
        blobs = {item['path']: item['sha'] for item in tree['tree'] if item['type'] == 'blob'}
        if any(blobs.get(path) != digest for path, digest in allowed.items()):
            raise ReceiptRefused('APPROVED_SOURCE_CHANGED')
        current_schema = {path for path in blobs if path.startswith('bot/database/postgres/')}
        approved_schema = {path for path in allowed if path.startswith('bot/database/postgres/')}
        if current_schema != approved_schema:
            raise ReceiptRefused('SCHEMA_FILES_CHANGED')

    def _verify_signatures(self, request: dict) -> None:
        """@description: Delegate signature verification to gh with exact workflow/commit/ref policy.
        @param self: Verifier.
        @param request: Immutable artifact and provenance identity.
        @return: None; a nonempty verified result with matching certificate is required.
        """
        results = self.command(['attestation', 'verify', f"oci://{IMAGE}@{request['digest']}",
                                '--repo', REPOSITORY, '--hostname', 'github.com',
                                '--signer-workflow', f'{REPOSITORY}/{WORKFLOW}',
                                '--source-ref', SOURCE_REF, '--source-digest', request['commit'],
                                '--deny-self-hosted-runners', '--predicate-type', PREDICATE,
                                '--cert-oidc-issuer', 'https://token.actions.githubusercontent.com', '--format', 'json'])
        if not isinstance(results, list) or not results:
            raise ReceiptRefused('NO_VERIFIED_ATTESTATION')
        if not any(matches_verified_result(item, request) for item in results):
            raise ReceiptRefused('VERIFIED_PROVENANCE_MISMATCH')


def matches_verified_result(item: dict, request: dict) -> bool:
    """@description: Add exact case-sensitive certificate checks and bind the specific run/attempt.
    @param item: Result already cryptographically verified by gh, not caller JSON.
    @param request: Expected release request.
    @return: Whether subject, certificate and invocation all match.
    """
    try:
        verified = item['verificationResult']
        certificate = verified['signature']['certificate']
        statement = verified['statement']
        invocation = f"https://github.com/{REPOSITORY}/actions/runs/{request['run']}/attempts/{request['attempt']}"
        expected = {'sourceRepositoryRef': SOURCE_REF, 'sourceRepositoryDigest': request['commit'],
                    'sourceRepositoryIdentifier': str(REPOSITORY_ID), 'buildTrigger': 'push',
                    'buildConfigURI': f'https://github.com/{REPOSITORY}/{WORKFLOW}@{SOURCE_REF}'}
        if any(certificate.get(key) != value for key, value in expected.items()):
            return False
        subjects = statement['subject']
        matched = any(subject.get('name') == IMAGE and subject.get('digest', {}).get('sha256') == request['digest'][7:]
                      for subject in subjects)
        return (matched and statement['predicateType'] == PREDICATE and bool(verified['verifiedTimestamps']) and
                statement['predicate']['runDetails']['metadata']['invocationId'] == invocation)
    except (KeyError, TypeError, AttributeError):
        return False
