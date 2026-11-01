"""Run the actual deployment shell with synthetic SSH responses and no network.

These tests preserve the distinction between a host refusal and lost transport.
No production credential, SSH server, queue or Docker daemon is accessed.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_SSH = '''import json, os, pathlib, sys
root = pathlib.Path(os.environ['JB_FAKE_SSH_STATE'])
requests = json.loads((root / 'requests.json').read_text())
responses = json.loads((root / 'responses.json').read_text())
response, code = responses[min(len(requests), len(responses) - 1)]
requests.append(sys.stdin.read())
(root / 'requests.json').write_text(json.dumps(requests))
print(response)
sys.exit(code)
'''


def run_deployment(tmp_path, responses):
    """@description: Execute the shipped workflow shell against only fake local tools.
    @param tmp_path: Private filesystem scope for synthetic credentials and transcripts.
    @param responses: Ordered stdout and exit codes for submission and status queries.
    @return: Completed shell process and all synthetic SSH stdin requests.
    """
    tools = tmp_path / 'tools'
    tools.mkdir()
    (tmp_path / 'responses.json').write_text(json.dumps(responses))
    (tmp_path / 'requests.json').write_text('[]')
    (tools / 'ssh').write_text(f'#!{sys.executable}\n' + FAKE_SSH)
    (tools / 'sleep').write_text('#!/bin/sh\nexit 0\n')
    for path in tools.iterdir():
        path.chmod(0o755)
    source = (ROOT / '.github/workflows/deploy.yml').read_text()
    script = textwrap.dedent(source.split('        run: |\n', 1)[1])
    environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'],
                       JB_FAKE_SSH_STATE=str(tmp_path), RUNNER_TEMP=str(tmp_path),
                       COMMIT='a' * 40, DIGEST='sha256:' + 'b' * 64,
                       RELEASE_RUN='42', RELEASE_ATTEMPT='1', DEPLOY_HOST='lab.invalid',
                       DEPLOY_SSH_KEY='SYNTHETIC_KEY', DEPLOY_KNOWN_HOSTS='SYNTHETIC_HOST')
    result = subprocess.run(['bash', '-c', script], env=environment, text=True,
                            capture_output=True, timeout=15)
    return result, json.loads((tmp_path / 'requests.json').read_text())


@pytest.mark.parametrize('code', [0, 2])
def test_host_rejection_is_terminal_even_with_nonzero_exit(tmp_path, code):
    """@description: Stop on a confirmed host rejection instead of masking it as transport loss.
    @param tmp_path: Private fake SSH scope.
    @param code: Supported exit code accompanying the rejected protocol word.
    @return: None.
    """
    result, requests = run_deployment(tmp_path, [('rejected', code)])
    assert result.returncode == 1
    assert 'Deployment rejected' in result.stdout
    assert 'No confirmed success' not in result.stdout
    assert len(requests) == 2


def test_lost_submission_response_is_reconciled_without_resubmitting(tmp_path):
    """@description: Recover a lost submission response using only the same durable status identity.
    @param tmp_path: Private fake SSH scope.
    @return: None.
    """
    responses = [('', 255), ('pending', 0), ('succeeded', 0)]
    result, requests = run_deployment(tmp_path, responses)
    assert result.returncode == 0 and 'Deployment succeeded' in result.stdout
    assert sum(line.startswith('deploy-v1 ') for line in requests) == 1
    assert requests[1:] == ['status-v1 42 1\n'] * 2


@pytest.mark.parametrize('status,code', [('rolled_back', 0), ('rollback_failed', 0),
                                       ('succeeded', 2), ('unexpected', 0)])
def test_only_successful_transport_with_success_word_can_succeed(tmp_path, status, code):
    """@description: Keep failed recovery and malformed protocol responses from becoming success.
    @param tmp_path: Private fake SSH scope.
    @param status: Synthetic terminal or invalid response.
    @param code: SSH exit code to validate together with stdout.
    @return: None.
    """
    result, requests = run_deployment(tmp_path, [('pending', 0), (status, code)])
    assert result.returncode == 1 and 'Deployment succeeded' not in result.stdout
    assert len(requests) == 2


def test_transport_failure_is_bounded_and_never_reports_success(tmp_path):
    """@description: Retain bounded read-only reconciliation after SSH transport failures.
    @param tmp_path: Private fake SSH scope.
    @return: None.
    """
    result, requests = run_deployment(tmp_path, [('succeeded', 255)])
    assert result.returncode == 1 and 'No confirmed success' in result.stdout
    assert 'Deployment succeeded' not in result.stdout
    assert len(requests) == 121
