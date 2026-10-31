"""Exercise checked-in workflow gates and a receipt-to-notification simulation.

The tiny expression reader accepts only the equality/conjunction subset currently
used by these jobs; unsupported workflow syntax fails rather than being guessed.
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


def job_gate(workflow: str, job: str, context: dict) -> bool:
    """@description: Evaluate the actual checked-in job condition without eval or duplicated policy.
    @param workflow: Workflow filename.
    @param job: Fixed job identifier.
    @param context: Synthetic flattened GitHub context strings.
    @return: Whether all supported equality terms pass; changed syntax is a test failure.
    """
    source = (WORKFLOWS / workflow).read_text()
    block = re.search(r'(?m)^  ' + re.escape(job) + r':\n((?:^    .*\n|^\n)+)', source)
    assert block is not None
    condition = re.search(r'(?m)^    if: (.+(?:\n      .+)*)', block[1])
    assert condition is not None
    expression = condition[1].removeprefix('>-').strip()
    comparisons = []
    for term in expression.split('&&'):
        match = re.fullmatch(r"\s*([\w.]+) == ('[^']*'|[\w.]+)\s*", term)
        assert match is not None, 'Unsupported workflow expression: ' + term
        left, right = match.groups()
        right_value = right[1:-1] if right.startswith("'") else context.get(right, '')
        left_value = context.get(left, '')
        assert isinstance(left_value, str) and isinstance(right_value, str), 'Only string contexts are supported'
        comparisons.append(left_value.casefold() == right_value.casefold())
    return all(comparisons)


def deploy_context(**changes) -> dict:
    """@description: Supply fake event context for the real resolve condition.
    @param changes: Flattened context overrides.
    @return: Independent synthetic context, never GitHub repository variables.
    """
    return {'github.repository': 'andrewchu0101/JukeBox101', 'vars.CD_ENABLED': 'true',
            'github.event.workflow_run.conclusion': 'success', 'github.event.workflow_run.event': 'push',
            'github.event.workflow_run.head_repository.full_name': 'andrewchu0101/JukeBox101', **changes}


@pytest.mark.parametrize('field,value', [
    ('vars.CD_ENABLED', 'false'), ('vars.CD_ENABLED', ''),
    ('github.repository', 'fork/JukeBox101'), ('github.event.workflow_run.conclusion', 'failure'),
    ('github.event.workflow_run.conclusion', 'cancelled'), ('github.event.workflow_run.event', 'pull_request'),
    ('github.event.workflow_run.head_repository.full_name', 'fork/JukeBox101'),
])
def test_workflow_gate_skips_before_request_resolution(field, value):
    """@description: A false real workflow condition leads to skipped deployment notification.
    @param field: Synthetic context field varied.
    @param value: Value which must not enter the privileged path.
    @return: None; the resolver, queue and backend are never constructed in this skipped path.
    """
    assert job_gate('deploy.yml', 'resolve', deploy_context())
    assert not job_gate('deploy.yml', 'resolve', deploy_context(**{field: value}))
    run = dict(NOTIFY_RUN, name='Deploy', path='.github/workflows/deploy.yml', event='workflow_run')
    api = FakeGitHub(run, outcome='skipped')
    api.jobs['jobs'][0]['conclusion'] = 'skipped'
    assert publish({'workflow_run': run}, api)
    assert '未執行部署' in api.writes[0] and '部署完成' not in api.writes[0]


@pytest.mark.parametrize('enabled', ['true', 'TRUE', 'True'])
def test_workflow_string_equality_ignores_case(enabled):
    """@description: Match GitHub's documented case-insensitive string equality without changing the real gate.
    @param enabled: Supported spelling of the synthetic enabled variable.
    @return: None; only the test context changes, never repository variables.
    """
    assert job_gate('deploy.yml', 'resolve', deploy_context(**{'vars.CD_ENABLED': enabled}))


def test_pr_ci_release_and_notify_trigger_contract():
    """@description: PR targets CI while Release stays main-push-only and Notify stays independent.
    @param: none
    @return: None; these are source contracts, not a hosted Actions scheduler test.
    """
    ci = (WORKFLOWS / 'ci.yml').read_text().split('permissions:', 1)[0]
    release = (WORKFLOWS / 'release.yml').read_text().split('permissions:', 1)[0]
    assert 'pull_request:\n    branches: [main]' in ci and 'workflow_call:' in ci
    assert 'push:\n    branches: [main]' in release and 'pull_request' not in release
    assert job_gate('release.yml', 'verify', {'github.repository': 'andrewchu0101/JukeBox101'})
    assert not job_gate('release.yml', 'verify', {'github.repository': 'fork/JukeBox101'})
    deploy = (WORKFLOWS / 'deploy.yml').read_text().split('\n  deploy:', 1)[1]
    assert 'needs: resolve' in deploy and '\n    if:' not in deploy
    assert job_gate('notify.yml', 'notify', {'github.repository': 'andrewchu0101/JukeBox101'})
    assert not job_gate('notify.yml', 'notify', {'github.repository': 'fork/JukeBox101'})


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
