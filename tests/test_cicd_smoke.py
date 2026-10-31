"""Exercise checked-in workflow gates and a receipt-to-notification simulation.

Automatic PR/main jobs select only the safe test entrypoint. Receipt and notification
fixtures exercise isolated or historical contracts, not deferred formal workflows.
"""

import json
import re
from pathlib import Path

import pytest

from deploy.controller import Controller
from deploy.github_notifications import publish
from deploy.host_worker import run_queue
from deploy.lab_backend import ProcessLabBackend, initialize
from deploy.queue import RequestQueue
from deploy.release_request import resolve
from tests.test_github_notifications import FakeGitHub, RUN as NOTIFY_RUN
from tests.test_release_request import COMMIT, RUN, archive_for

WORKFLOWS = Path(__file__).resolve().parents[1] / '.github/workflows'
OLD = 'sha256:' + '1' * 64


def test_pr_and_main_select_only_the_safe_ci_job():
    """@description: PR/main automation selects the isolated test job without a formal release dependency.
    @param: none
    @return: None; source contracts complement the real hosted PR CI run.
    """
    ci = (WORKFLOWS / 'ci.yml').read_text()
    triggers = ci.split('permissions:', 1)[0]
    assert "'on':\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]" in triggers
    assert 'workflow_call' not in triggers and 'workflow_run' not in triggers
    assert re.findall(r'(?m)^  ([a-z_]+):$', ci.split('jobs:', 1)[1]) == ['verify']
    assert 'needs:' not in ci and 'uses: ./.github/workflows/' not in ci
    notify = (WORKFLOWS / 'notify.yml').read_text()
    assert 'workflows: [CI]' in notify


@pytest.mark.parametrize('workflow,event', [('CI', 'pull_request'), ('Release', 'push')])
@pytest.mark.parametrize('outcome,expected', [
    ('success', '通過'), ('failure', '失敗'), ('cancelled', '已取消'), ('timed_out', '逾時'),
    ('action_required', '需要處理'), ('neutral', '中立結果'), ('skipped', '未執行'),
    ('startup_failure', '啟動失敗'), ('stale', '已過期'),
])
def test_all_non_deploy_notification_categories(workflow, event, outcome, expected):
    """@description: Format every supported conclusion through the real publisher and fake API.
    @param workflow: Fixed notification workflow.
    @param event: Its permitted trigger event.
    @param outcome: GitHub conclusion to classify.
    @param expected: Independently specified user-visible classification.
    @return: None; no notification leaves the test process.
    """
    run = dict(NOTIFY_RUN, name=workflow, path=f'.github/workflows/{workflow.lower()}.yml',
               event=event, conclusion=outcome)
    api = FakeGitHub(run)
    assert publish({'workflow_run': run}, api)
    assert f'{workflow}：{expected}' in api.writes[0]


def resolved_fake_request() -> dict:
    """@description: Feed a real receipt ZIP through the existing resolver with exact fake endpoints.
    @param: none
    @return: Parsed fake request; unknown API calls fail without any network fallback.
    """
    prefix = 'repos/andrewchu0101/JukeBox101'
    responses = {f'{prefix}/branches/main': json.dumps({'commit': {'sha': COMMIT}}).encode(),
                 f'{prefix}/actions/runs/42/attempts/2': json.dumps(RUN).encode(),
                 f'{prefix}/actions/runs/42/artifacts?per_page=100': json.dumps(dict(total_count=1,
                     artifacts=[dict(id=99, expired=False, name='release-receipt-42-2')])).encode(),
                 f'{prefix}/actions/artifacts/99/zip': archive_for()}
    return resolve({'workflow_run': RUN}, responses.__getitem__)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'incomplete', 'unfinished'])
def test_ambiguous_deploy_jobs_never_report_completion(fault):
    """@description: Incomplete or ambiguous job evidence must not produce a deployment success message.
    @param fault: Synthetic jobs response defect.
    @return: None; incomplete pagination fails explicitly, other unknown states remain unconfirmed.
    """
    run = dict(NOTIFY_RUN, name='Deploy', path='.github/workflows/deploy.yml', event='workflow_run')
    api = FakeGitHub(run)
    if fault == 'missing':
        api.jobs['jobs'].pop()
        api.jobs['total_count'] = 1
    elif fault == 'duplicate':
        api.jobs['jobs'].append(dict(api.jobs['jobs'][1]))
        api.jobs['total_count'] = 3
    elif fault == 'incomplete':
        api.jobs['total_count'] = 3
    else:
        api.jobs['jobs'][1]['status'] = 'in_progress'
    if fault == 'incomplete':
        with pytest.raises(ValueError, match='JOB_LIST_INCOMPLETE'):
            publish({'workflow_run': run}, api)
        assert api.writes == []
    else:
        assert publish({'workflow_run': run}, api)
        assert '部署完成' not in api.writes[0]


@pytest.mark.parametrize('health,expected', [('healthy', 'succeeded'), ('failed', 'rolled_back')])
def test_receipt_queue_switch_and_notification(tmp_path, health, expected):
    """@description: Join the existing resolver, durable queue, real fake process and notification publisher.
    @param tmp_path: Private lab scope with no mounts or production state.
    @param health: Synthetic candidate health.
    @param expected: Durable result; rollback must yield a failed deployment notification.
    @return: None; permissive authorization is lab-only and proves no provenance trust.
    """
    request = resolved_fake_request()
    initialize(tmp_path, OLD, {request['digest']: health})
    backend = ProcessLabBackend(tmp_path)
    try:
        backend.start(OLD)
        queue = RequestQueue(tmp_path / 'requests')
        assert queue.submit(request) == 'pending'
        assert queue.submit(request) == 'pending'
        assert run_queue(queue.directory, Controller(tmp_path, backend, authorize=lambda _: True)) == [expected]
        generation = (tmp_path / 'active.json').read_text()
        assert queue.submit(request) == expected and run_queue(queue.directory, None) == []
        assert (tmp_path / 'active.json').read_text() == generation
        assert backend.healthy(request['digest'] if expected == 'succeeded' else OLD)
        run = dict(NOTIFY_RUN, name='Deploy', path='.github/workflows/deploy.yml', event='workflow_run')
        api = FakeGitHub(run, outcome='success' if expected == 'succeeded' else 'failure')
        assert publish({'workflow_run': run}, api)
        assert ('部署完成' in api.writes[0]) == (expected == 'succeeded')
    finally:
        backend.stop()
