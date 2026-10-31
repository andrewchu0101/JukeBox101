"""Report completed CI/CD runs to a fixed subscribed GitHub issue.

Only trusted default-branch code runs with issue-write permission. PR artifacts,
titles, logs, credentials and user content never enter notification messages.
"""

import json
import os
import re
import subprocess
from pathlib import Path

from deploy.receipt import REPOSITORY, REPOSITORY_ID

NOTIFICATION_ISSUE = 7
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
WORKFLOWS = {
    '.github/workflows/ci.yml': ('CI', {'push', 'pull_request'}),
    '.github/workflows/release.yml': ('Release', {'push'}),
    '.github/workflows/deploy.yml': ('Deploy', {'workflow_run'}),
}
RESULTS = {'success': '通過', 'failure': '失敗', 'cancelled': '已取消',
           'timed_out': '逾時', 'action_required': '需要處理', 'neutral': '中立結果',
           'skipped': '未執行', 'startup_failure': '啟動失敗', 'stale': '已過期'}


def github_api(endpoint: str, *, method='GET', body=None, paginate=False):
    """@description: Send constructed GitHub requests without printing credentials or API diagnostics.
    @param endpoint: Fixed repository path plus validated numeric identifiers.
    @param method: GET or POST selected by the notification implementation.
    @param body: Optional synthetic-safe comment body, never workflow logs.
    @param paginate: Collect comment pages to preserve run/attempt idempotency.
    @return: Parsed bounded JSON; any failed request fails this notification job only.
    """
    arguments = ['gh', 'api', '--method', method, endpoint]
    if paginate:
        arguments.extend(['--paginate', '--slurp'])
    if body is not None:
        arguments.extend(['--input', '-'])
    source = json.dumps(body).encode() if body is not None else None
    result = subprocess.run(arguments, input=source, capture_output=True, timeout=90)
    if result.returncode or len(result.stdout) > MAX_RESPONSE_BYTES:
        raise RuntimeError('NOTIFICATION_API_FAILED')
    return json.loads(result.stdout)


def run_identity(run: dict) -> tuple[int, int]:
    """@description: Validate identifiers before constructing an API endpoint or a comment marker.
    @param run: Workflow event or authoritative API identity.
    @return: Positive bounded run and attempt identifiers.
    """
    for field, limit in (('id', 20), ('run_attempt', 6)):
        value = run.get(field)
        if type(value) is not int or not 0 < value < 10 ** limit:
            raise ValueError('NOTIFICATION_RUN_ID_INVALID')
    return run['id'], run['run_attempt']


def verified_run(event: dict, api) -> dict:
    """@description: Re-read one exact completed run from the owning repository before reporting it.
    @param event: GitHub workflow_run payload; all fields are initially untrusted.
    @param api: GitHub network boundary, replaced with synthetic responses in tests.
    @return: Completed fixed-repository workflow metadata with no arbitrary content selected.
    """
    run, attempt = run_identity(event['workflow_run'])
    current = api(f'repos/{REPOSITORY}/actions/runs/{run}/attempts/{attempt}')
    if run_identity(current) != (run, attempt) or current.get('repository', {}).get('id') != REPOSITORY_ID:
        raise ValueError('NOTIFICATION_REPOSITORY_OR_ATTEMPT')
    if current.get('status') != 'completed' or current.get('conclusion') not in RESULTS:
        raise ValueError('NOTIFICATION_RESULT_NOT_COMPLETE')
    if not re.fullmatch(r'[a-f0-9]{40}', current.get('head_sha', '')):
        raise ValueError('NOTIFICATION_COMMIT_INVALID')
    identity = WORKFLOWS.get(current.get('path'))
    if identity is None or current.get('name') != identity[0] or current.get('event') not in identity[1]:
        raise ValueError('NOTIFICATION_WORKFLOW_MISMATCH')
    return current


def deployment_result(run: dict, api) -> str:
    """@description: Distinguish a real successful host deployment from skipped jobs or failed orchestration.
    @param run: Validated Deploy run.
    @param api: GitHub jobs lookup boundary.
    @return: Truthful deployment summary without inventing rollback or active-host state.
    """
    endpoint = f"repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
    listing = api(endpoint)
    if listing['total_count'] != len(listing['jobs']):
        raise ValueError('NOTIFICATION_JOB_LIST_INCOMPLETE')
    matches = [job for job in listing['jobs'] if job.get('name') == 'deploy']
    if len(matches) != 1:
        return '未確認部署；請查看 Actions'
    outcome = matches[0].get('conclusion')
    if outcome == 'skipped':
        return '未執行部署（部署條件未成立）'
    if outcome == 'success' and matches[0].get('status') == 'completed':
        return '部署完成（主機已回報健康檢查通過）'
    return '部署未成功；請在 Actions 確認主機狀態與回滾結果'


def comment_body(run: dict, result: str) -> str:
    """@description: Format only fixed workflow names, safe status and strictly validated identifiers.
    @param run: Verified run identity.
    @param result: Implementation-selected status text, never untrusted log content.
    @return: Bounded comment with a canonical Actions link and idempotency marker.
    """
    marker = f"<!-- jukebox101-actions:{run['id']}:{run['run_attempt']} -->"
    url = f"https://github.com/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}"
    return (f"{marker}\n**{run['name']}：{result}**\n\n"
            f"Workflow commit：`{run['head_sha'][:12]}` · attempt {run['run_attempt']}\n\n"
            f"[查看 Actions 結果與日誌]({url})\n")


def publish(event: dict, api=github_api) -> bool:
    """@description: Append each run/attempt result once to the fixed subscribed issue, independently of CD.
    @param event: Completed workflow_run event.
    @param api: GitHub network boundary, synthetic only in tests.
    @return: True when posted, False for an already-published bot result; failures raise.
    """
    run = verified_run(event, api)
    result = deployment_result(run, api) if run['name'] == 'Deploy' else RESULTS[run['conclusion']]
    body = comment_body(run, result)
    marker = body.split('\n', 1)[0]
    endpoint = f'repos/{REPOSITORY}/issues/{NOTIFICATION_ISSUE}'
    issue = api(endpoint)
    if issue.get('state') != 'open' or 'pull_request' in issue:
        raise ValueError('NOTIFICATION_ISSUE_UNAVAILABLE')
    pages = api(f'{endpoint}/comments?per_page=100', paginate=True)
    comments = [comment for page in pages for comment in page]
    if any(comment.get('user', {}).get('login') == 'github-actions[bot]' and
           marker in comment.get('body', '') for comment in comments):
        return False
    api(f'{endpoint}/comments', method='POST', body={'body': body})
    return True


def main() -> None:
    """@description: Post a safe GitHub notification using only the main-branch workflow's scoped token.
    @param: none
    @return: None; notifier failure never rewrites a Release or Deploy result.
    """
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    print('posted' if publish(event) else 'already_posted')


if __name__ == '__main__':
    main()
