"""Protect CI publication boundaries and closed runtime routes without credentials.

The SSH helper is tested as a subprocess, while runtime policy stays offline.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from bot.runtime_policy import check_policy

ROOT = Path(__file__).resolve().parents[1]


def test_ci_has_no_publication_or_production_secret():
    """@description: Prevent active CI from gaining publishing, SSH or unsafe PR execution paths.
    @param: none
    @return: None.
    """
    source = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text()
    for forbidden in ('pull_request_target', 'secrets.', 'packages: write', 'id-token: write',
                      'docker push', 'environment: production', 'self-hosted', 'ssh ', 'workflow_call',
                      'verify-fresh-postgres', 'migrate', 'runtime_smoke', 'docker save', 'export_runtime'):
        assert forbidden not in source
    assert 'persist-credentials: false' in source
    assert 'permissions: {}' in source
    commands = [line.split('run:', 1)[1].strip() for line in source.splitlines() if line.strip().startswith('run:')]
    assert commands == ['docker build --pull -f test.Dockerfile -t jukebox101-test:v7-local .',
                        'python3 -m scripts.ci.cd_smoke']
    for line in source.splitlines():
        if 'uses:' in line:
            import re
            assert re.search(r'@[a-f0-9]{40}$', line)


@pytest.mark.parametrize('workflow', ['release.yml', 'deploy.yml'])
def test_formal_workflows_have_no_automatic_trigger_or_live_capability(workflow):
    """@description: Merging or manually selecting a reserved formal workflow cannot publish or reach a VPS.
    @param workflow: Deferred release or deployment definition.
    @return: None.
    """
    source = (ROOT / '.github' / 'workflows' / workflow).read_text()
    assert "'on':\n  workflow_dispatch:\n\npermissions: {}" in source
    assert '    if: ${{ false }}' in source
    assert [line.strip() for line in source.splitlines() if line.startswith('  ') and not line.startswith('    ')] == [
        'workflow_dispatch:', 'deferred:']
    assert [line.strip() for line in source.splitlines() if line.strip().startswith('run:')] == [
        'run: echo "' + ('Image publication' if workflow == 'release.yml' else 'VPS deployment') +
        ' is deferred to a separate reviewed change."']
    for forbidden in ('push:', 'pull_request:', 'workflow_run:', 'schedule:', 'workflow_call:', 'uses:',
                      'secrets.', 'vars.', 'write', 'environment:', 'docker ', 'gh ', 'ssh ', 'python'):
        assert forbidden not in source


def test_notifications_do_not_execute_triggering_pr_code_or_hold_deployment_secrets():
    """@description: Keep shared result delivery on trusted default-branch code with issue-only writes.
    @param: none
    @return: None.
    """
    source = (ROOT / '.github' / 'workflows' / 'notify.yml').read_text()
    assert 'workflows: [CI]' in source
    assert 'ref: ${{ github.sha }}' in source and 'persist-credentials: false' in source
    assert 'issues: write' in source and 'actions: read' in source
    assert 'github.event.workflow_run.head_sha' not in source
    assert all(forbidden not in source for forbidden in ('secrets.', 'packages: write', 'id-token: write',
                                                        'pull_request_target', 'download-artifact', 'ssh '))
    deploy = (ROOT / '.github' / 'workflows' / 'deploy.yml').read_text()
    assert 'needs: notify' not in deploy


@pytest.mark.parametrize('request_line', [
    f'deploy-v1 {"a"*40} sha256:{"b"*64} 10 1\n',
    'status-v1 10 1\n', 'status-v1 10 1\nextra', 'x' * 258,
])
def test_host_helper_always_refuses_unverified_deployment(request_line):
    """@description: No env switch or syntactically valid request enables missing receipt verification.
    @param request_line: Synthetic SSH input.
    @return: None.
    """
    result = subprocess.run([sys.executable, '-m', 'deploy.ssh_request'], input=request_line,
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 2 and result.stdout == 'rejected\n'


def test_runtime_closed_contract(monkeypatch):
    """@description: Verify the real local registry and disabled business routes without connections.
    @param monkeypatch: Set only non-secret policy values.
    @return: None.
    """
    monkeypatch.setenv('JUKEBOX_FORMAL_EXCHANGE', 'off')
    monkeypatch.setenv('JUKEBOX_NICKEL_CLAIM', 'off')
    assert asyncio.run(check_policy())['claim_registered'] is False
