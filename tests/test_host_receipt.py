"""Test release authorization policy at a fake official-CLI boundary.

These are policy unit tests, not real cryptographic, registry or GitHub acceptance.
"""

import json
from types import SimpleNamespace

import pytest

from deploy.controller import parse_request
from deploy.receipt import GithubReceiptVerifier, IMAGE, PREDICATE, REPOSITORY, REPOSITORY_ID, ReceiptRefused, WORKFLOW

REQUEST = parse_request(f'deploy-v1 {"a" * 40} sha256:{"b" * 64} 42 2')
SCHEMA = 'bot/database/postgres/0001_initial_schema.sql'


def verified_result():
    """@description: Model the documented gh verified JSON shape using only synthetic data.
    @param: none
    @return: Unit fixture; it is never accepted as real signature evidence.
    """
    certificate = {'sourceRepositoryRef': 'refs/heads/main', 'sourceRepositoryDigest': REQUEST['commit'],
                   'sourceRepositoryIdentifier': str(REPOSITORY_ID), 'buildTrigger': 'push',
                   'buildConfigURI': f'https://github.com/{REPOSITORY}/{WORKFLOW}@refs/heads/main'}
    invocation = f'https://github.com/{REPOSITORY}/actions/runs/42/attempts/2'
    statement = {'subject': [{'name': IMAGE, 'digest': {'sha256': 'b' * 64}}], 'predicateType': PREDICATE,
                 'predicate': {'runDetails': {'metadata': {'invocationId': invocation}}}}
    return {'verificationResult': {'signature': {'certificate': certificate}, 'statement': statement,
                                   'verifiedTimestamps': [{'synthetic': True}]}}


@pytest.fixture
def receipt():
    """@description: Supply an isolated mutable command transcript with no network or credential.
    @param: none
    @return: Enabled unit policy, fake responses and recorded subprocess calls.
    """
    blobs = {WORKFLOW: '1' * 40, '.github/workflows/ci.yml': '2' * 40, SCHEMA: '3' * 40}
    policy = {'enabled': True, 'crypto_acceptance_verified': True, 'approved_blobs': blobs}
    run = dict(id=42, run_attempt=2, head_sha=REQUEST['commit'], head_branch='main', event='push',
               status='completed', conclusion='success', path=WORKFLOW, repository={'id': REPOSITORY_ID})
    tree = {'truncated': False, 'tree': [{'path': path, 'sha': sha, 'type': 'blob'} for path, sha in blobs.items()]}
    responses = [{'id': REPOSITORY_ID}, {'commit': {'sha': REQUEST['commit']}}, run, tree, [verified_result()]]
    calls = []

    def runner(argv, **kwargs):
        """@description: Return synthetic JSON at the subprocess boundary.
        @param argv: Recorded argument array.
        @param kwargs: Recorded process environment and bounds.
        @return: Successful synthetic CLI result.
        """
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(responses[len(calls) - 1]))

    return SimpleNamespace(policy=policy, responses=responses, calls=calls, runner=runner)


def test_receipt_requires_exact_cli_policy(receipt):
    """@description: Bind digest, main, workflow, run and hosted provenance with fixed argv.
    @param receipt: Synthetic official-CLI boundary.
    @return: None.
    """
    assert GithubReceiptVerifier(receipt.policy, receipt.runner).verify(REQUEST)
    argv, options = receipt.calls[-1]
    assert argv[:3] == ['/usr/bin/gh', 'attestation', 'verify']
    assert f'oci://{IMAGE}@{REQUEST["digest"]}' in argv
    assert '--deny-self-hosted-runners' in argv and '--source-digest' in argv
    assert argv[argv.index('--source-ref') + 1] == 'refs/heads/main'
    assert options['timeout'] == 60 and options['cwd'] == '/'
    assert set(options['env']) == {'PATH', 'HOME', 'GH_HOST', 'GH_PROMPT_DISABLED', 'GH_CONFIG_DIR', 'DOCKER_CONFIG'}
    assert options['env']['DOCKER_CONFIG'] == '/etc/jukebox101/docker'


@pytest.mark.parametrize('field,value', [('enabled', False), ('crypto_acceptance_verified', False)])
def test_disabled_receipt_runs_no_cli(receipt, field, value):
    """@description: Refuse unattended verification until explicit independent acceptance exists.
    @param receipt: Fake CLI fixture.
    @param field: Required gate.
    @param value: Disabled gate value.
    @return: None.
    """
    receipt.policy[field] = value
    with pytest.raises(ReceiptRefused):
        GithubReceiptVerifier(receipt.policy, receipt.runner).verify(REQUEST)
    assert receipt.calls == []


@pytest.mark.parametrize('index,key,value', [
    (0, 'id', 999), (1, 'commit', {'sha': 'c' * 40}), (2, 'event', 'pull_request'),
    (2, 'path', '.github/workflows/untrusted.yml'), (2, 'status', 'in_progress'),
    (2, 'conclusion', 'failure'), (2, 'run_attempt', 1), (2, 'repository', {'id': 999}),
    (3, 'truncated', True),
])
def test_source_and_run_mismatch_refused(receipt, index, key, value):
    """@description: Reject changed source or untrusted workflow before using its attestation.
    @param receipt: Fake CLI fixture.
    @param index: Response to mutate.
    @param key: Identity field to replace.
    @param value: Invalid field value.
    @return: None.
    """
    receipt.responses[index][key] = value
    with pytest.raises(ReceiptRefused):
        GithubReceiptVerifier(receipt.policy, receipt.runner).verify(REQUEST)


@pytest.mark.parametrize('field,value', [
    ('sourceRepositoryRef', 'refs/heads/Main'), ('sourceRepositoryDigest', 'c' * 40),
    ('sourceRepositoryIdentifier', '999'), ('buildTrigger', 'pull_request_target'),
    ('buildConfigURI', f'https://github.com/{REPOSITORY}/other.yml@refs/heads/main'),
])
def test_certificate_exact_fields_refused(receipt, field, value):
    """@description: Recheck exact case and numeric repository identity after CLI verification.
    @param receipt: Synthetic CLI fixture.
    @param field: Certificate extension field.
    @param value: Mismatching value.
    @return: None.
    """
    receipt.responses[-1][0]['verificationResult']['signature']['certificate'][field] = value
    with pytest.raises(ReceiptRefused):
        GithubReceiptVerifier(receipt.policy, receipt.runner).verify(REQUEST)


@pytest.mark.parametrize('fault', ['subject', 'digest', 'invocation', 'timestamps', 'empty', 'schema_added', 'schema_changed'])
def test_attestation_and_schema_binding_refused(receipt, fault):
    """@description: Reject other artifacts, attempts and implicit schema changes.
    @param receipt: Synthetic CLI fixture.
    @param fault: Distinct trust-boundary mismatch.
    @return: None.
    """
    verified = receipt.responses[-1][0]['verificationResult']
    match fault:
        case 'subject':
            verified['statement']['subject'][0]['name'] = 'ghcr.io/other/bot'
        case 'digest':
            verified['statement']['subject'][0]['digest']['sha256'] = 'c' * 64
        case 'invocation':
            verified['statement']['predicate']['runDetails']['metadata']['invocationId'] += '0'
        case 'timestamps':
            verified['verifiedTimestamps'] = []
        case 'empty':
            receipt.responses[-1] = []
        case 'schema_added':
            receipt.responses[3]['tree'].append({'path': SCHEMA + '.new', 'sha': '4' * 40, 'type': 'blob'})
        case _:
            receipt.responses[3]['tree'][-1]['sha'] = '4' * 40
    with pytest.raises(ReceiptRefused):
        GithubReceiptVerifier(receipt.policy, receipt.runner).verify(REQUEST)


@pytest.mark.parametrize('output,returncode', [('not json', 0), ('sensitive diagnostic', 1), ('[]', 1)])
def test_cli_failure_does_not_echo_output(output, returncode):
    """@description: Suppress upstream diagnostics and fail closed on malformed or failed verification.
    @param output: Synthetic unsafe or invalid CLI output.
    @param returncode: Simulated process status.
    @return: None.
    """
    runner = lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=output)
    with pytest.raises(ReceiptRefused) as caught:
        GithubReceiptVerifier({}, runner).command(['api', 'repos/example'])
    assert output not in str(caught.value)
