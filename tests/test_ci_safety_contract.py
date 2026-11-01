"""Protect CI publication boundaries and closed runtime routes without credentials.

Workflow text contracts and closed runtime routes are checked offline.
"""

import asyncio
import re
from pathlib import Path

from bot.runtime_policy import check_policy

ROOT = Path(__file__).resolve().parents[1]


def test_ci_has_no_publication_or_production_secret():
    """@description: Prevent active CI from gaining publishing, SSH or unsafe PR execution paths.
    @param: none
    @return: None.
    """
    source = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text()
    for forbidden in ('pull_request_target', 'secrets.', 'packages: write', 'id-token: write',
                      'docker push', 'environment: production', 'self-hosted', 'ssh '):
        assert forbidden not in source
    assert 'persist-credentials: false' in source
    assert 'permissions: {}' in source
    for line in source.splitlines():
        if 'uses:' in line:
            import re
            assert re.search(r'@[a-f0-9]{40}$', line)


def test_release_deploys_only_the_published_digest_behind_the_production_environment():
    """@description: Keep publishing behind verification and SSH behind a successful publish plus the kill switch.
    @param: none
    @return: None.
    """
    release = (ROOT / '.github' / 'workflows' / 'release.yml').read_text()
    assert 'uses: ./.github/workflows/ci.yml' in release and 'needs: verify' in release
    assert 'docker load' in release and 'docker build' not in release
    assert 'subject-digest: ${{ steps.image.outputs.digest }}' in release
    assert not (ROOT / '.github' / 'workflows' / 'deploy.yml').exists()
    secret_job = release.split('\n  deploy:', 1)[1]
    assert 'needs: publish' in secret_job and "vars.CD_ENABLED == 'true'" in secret_job
    assert 'environment: production' in secret_job and 'cancel-in-progress: false' in secret_job
    assert 'StrictHostKeyChecking=yes' in secret_job and 'DIGEST: ${{ needs.publish.outputs.digest }}' in secret_job
    assert 'checkout@' not in secret_job and 'GH_TOKEN' not in secret_job and 'permissions: {}' in secret_job
    assert 'pull_request_target' not in release and 'self-hosted' not in release
    for line in release.splitlines():
        if 'uses:' in line and '@' in line:
            assert re.search(r'@[a-f0-9]{40}$', line)


def test_notifications_do_not_execute_triggering_pr_code_or_hold_deployment_secrets():
    """@description: Keep shared result delivery on trusted default-branch code with issue-only writes.
    @param: none
    @return: None.
    """
    source = (ROOT / '.github' / 'workflows' / 'notify.yml').read_text()
    assert 'workflows: [CI, Release]' in source
    assert 'ref: ${{ github.sha }}' in source and 'persist-credentials: false' in source
    assert 'issues: write' in source and 'actions: read' in source
    assert 'github.event.workflow_run.head_sha' not in source
    assert all(forbidden not in source for forbidden in ('secrets.', 'packages: write', 'id-token: write',
                                                        'pull_request_target', 'download-artifact', 'ssh '))


def test_runtime_closed_contract(monkeypatch):
    """@description: Verify the real local registry and disabled business routes without connections.
    @param monkeypatch: Set only non-secret policy values.
    @return: None.
    """
    monkeypatch.setenv('JUKEBOX_FORMAL_EXCHANGE', 'off')
    monkeypatch.setenv('JUKEBOX_NICKEL_CLAIM', 'off')
    assert asyncio.run(check_policy())['claim_registered'] is False
