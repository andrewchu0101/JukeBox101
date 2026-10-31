"""Verify shared GitHub notifications at a fake API boundary.

Tests inspect actual emitted comment bodies and prove unsafe sources or failed
requests cannot masquerade as successful deployment notifications.
"""

import copy

import pytest

from deploy.github_notifications import NOTIFICATION_ISSUE, comment_body, publish
from deploy.receipt import REPOSITORY_ID

RUN = dict(id=42, run_attempt=2, head_sha='a' * 40, name='CI', path='.github/workflows/ci.yml',
           repository={'id': REPOSITORY_ID}, event='pull_request', status='completed', conclusion='success')


class FakeGitHub:
    """Record comment writes and expose only synthetic workflow/issue/job responses."""

    def __init__(self, run=None, comments=None, outcome='success'):
        """@description: Allocate independent fake state with no credential or network.
        @param self: Synthetic API.
        @param run: Run metadata override.
        @param comments: Existing comment pages for idempotency checks.
        @param outcome: Deploy job conclusion to model.
        @return: None.
        """
        self.run = copy.deepcopy(run or RUN)
        self.comments = comments or [[]]
        self.jobs = dict(total_count=2, jobs=[dict(name='resolve', status='completed', conclusion='success'),
                                            dict(name='deploy', status='completed', conclusion=outcome)])
        self.issue = {'state': 'open'}
        self.writes = []
        self.calls = []

    def __call__(self, endpoint, *, method='GET', body=None, paginate=False):
        """@description: Route constructed endpoints and record the actual posted body.
        @param self: Synthetic API.
        @param endpoint: Implementation-selected GitHub endpoint.
        @param method: Request method.
        @param body: Optional comment payload.
        @param paginate: Whether every comment page is required.
        @return: Deep-copied fake API response.
        """
        self.calls.append((endpoint, method, paginate))
        if method == 'POST':
            self.writes.append(body['body'])
            return {'id': 99}
        if endpoint.endswith('/jobs?per_page=100'):
            return copy.deepcopy(self.jobs)
        if endpoint.endswith('/comments?per_page=100'):
            assert paginate is True
            return copy.deepcopy(self.comments)
        if endpoint.endswith(f'/issues/{NOTIFICATION_ISSUE}'):
            return dict(self.issue)
        return copy.deepcopy(self.run)


def test_shared_notification_has_fixed_destination_and_no_pr_content():
    """@description: Publish a safe status to the subscribed issue without forwarding arbitrary content.
    @param: none
    @return: None.
    """
    run = dict(RUN, display_title='@everyone malicious title', html_url='https://untrusted.invalid/')
    api = FakeGitHub(run)
    assert publish({'workflow_run': RUN}, api)
    body = api.writes[0]
    assert 'CI：通過' in body and 'actions/runs/42/attempts/2' in body
    assert '@everyone' not in body and 'untrusted.invalid' not in body
    assert api.calls[-1][0].endswith('/issues/7/comments') and api.calls[-1][1] == 'POST'


def test_duplicate_bot_receipt_on_an_old_page_is_not_sent_again():
    """@description: Suppress the same run/attempt even when it is not on the latest comment page.
    @param: none
    @return: None.
    """
    comment = dict(user={'login': 'github-actions[bot]'}, body=comment_body(RUN, '通過'))
    api = FakeGitHub(comments=[[comment], []])
    assert publish({'workflow_run': RUN}, api) is False and api.writes == []


@pytest.mark.parametrize('fault', ['other_attempt', 'human_marker'])
def test_different_attempt_or_human_text_does_not_suppress_notification(fault):
    """@description: Keep rerun results visible and ignore human copies of a machine receipt marker.
    @param fault: Nonmatching existing comment category.
    @return: None.
    """
    prior = dict(RUN, run_attempt=1 if fault == 'other_attempt' else 2)
    author = 'someone' if fault == 'human_marker' else 'github-actions[bot]'
    api = FakeGitHub(comments=[[dict(user={'login': author}, body=comment_body(prior, '通過'))]])
    assert publish({'workflow_run': RUN}, api) and len(api.writes) == 1


@pytest.mark.parametrize('field,value', [
    ('id', 43), ('run_attempt', 1), ('repository', {'id': 99}), ('status', 'in_progress'),
    ('conclusion', 'unknown'), ('head_sha', 'a' * 40 + '\n@everyone'), ('name', '@everyone'),
    ('path', '.github/workflows/untrusted.yml'), ('event', 'pull_request_target'),
])
def test_invalid_workflow_never_posts(field, value):
    """@description: Reject source substitution before any notification write.
    @param field: API metadata field to change.
    @param value: Invalid value.
    @return: None.
    """
    api = FakeGitHub(dict(RUN, **{field: value}))
    with pytest.raises(ValueError):
        publish({'workflow_run': RUN}, api)
    assert api.writes == []


@pytest.mark.parametrize('field,value', [('id', '../42'), ('id', True), ('id', 10 ** 20),
                                       ('run_attempt', 0), ('run_attempt', '2'), ('run_attempt', 10 ** 6)])
def test_malformed_event_is_rejected_before_any_api_call(field, value):
    """@description: Refuse endpoint injection and unbounded event identifiers at the input boundary.
    @param field: Event identity field to alter.
    @param value: Non-numeric or out-of-range value.
    @return: None.
    """
    api = FakeGitHub()
    with pytest.raises(ValueError):
        publish({'workflow_run': dict(RUN, **{field: value})}, api)
    assert api.calls == [] and api.writes == []


@pytest.mark.parametrize('outcome,expected', [
    ('success', '部署完成（主機已回報健康檢查通過）'), ('skipped', '未執行部署'),
    ('failure', '部署未成功'), ('cancelled', '部署未成功'), ('timed_out', '部署未成功'),
])
def test_real_deploy_job_result_controls_the_message(outcome, expected):
    """@description: Do not call a skipped or failed deployment successful because other jobs passed.
    @param outcome: Synthetic deploy job result.
    @param expected: Truthful notification text fragment.
    @return: None.
    """
    run = dict(RUN, name='Deploy', path='.github/workflows/deploy.yml', event='workflow_run')
    api = FakeGitHub(run, outcome=outcome)
    assert publish({'workflow_run': RUN}, api) and expected in api.writes[0]
    if outcome != 'success':
        assert '部署完成' not in api.writes[0]


def test_issue_closed_by_maintainer_stops_notifications():
    """@description: Respect an unavailable destination without creating a new notification thread.
    @param: none
    @return: None.
    """
    api = FakeGitHub()
    api.issue['state'] = 'closed'
    with pytest.raises(ValueError):
        publish({'workflow_run': RUN}, api)
    assert api.writes == []


def test_api_failure_is_visible_and_does_not_publish_success():
    """@description: Surface delivery errors independently of the source deployment result.
    @param: none
    @return: None.
    """
    def failed_api(endpoint):
        """@description: Fail before exposing synthetic run metadata.
        @param endpoint: Constructed URL path.
        @return: Never.
        """
        raise RuntimeError('NOTIFICATION_API_FAILED')

    with pytest.raises(RuntimeError, match='NOTIFICATION_API_FAILED'):
        publish({'workflow_run': RUN}, failed_api)
