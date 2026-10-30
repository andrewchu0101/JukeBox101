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
    workflows = list((ROOT / '.github' / 'workflows').glob('*.yml'))
    assert len(workflows) == 1
    source = workflows[0].read_text()
    for forbidden in ('pull_request_target', 'secrets.', 'packages: write', 'id-token: write',
                      'docker push', 'environment: production', 'self-hosted', 'ssh '):
        assert forbidden not in source
    assert 'persist-credentials: false' in source
    assert 'permissions: {}' in source
    for line in source.splitlines():
        if 'uses:' in line:
            import re
            assert re.search(r'@[a-f0-9]{40}$', line)


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
