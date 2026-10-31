"""Resolve a completed main release into a bounded deployment request.

Artifacts carry identifiers, not authority: the host independently verifies the
run, current source, reviewed policy and cryptographic image provenance again.
"""

import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

from deploy.controller import parse_request
from deploy.receipt import REPOSITORY, REPOSITORY_ID, WORKFLOW

MAX_API_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_BYTES = 4096


def github_api(endpoint: str) -> bytes:
    """@description: Call only constructed GitHub API endpoints without echoing upstream errors.
    @param endpoint: Repository path built from constants and validated numeric identifiers.
    @return: Bounded bytes; authentication, network or API failures abort resolution.
    """
    result = subprocess.run(['gh', 'api', endpoint], capture_output=True, timeout=60)
    if result.returncode or len(result.stdout) > MAX_API_BYTES:
        raise RuntimeError('RELEASE_API_FAILED')
    return result.stdout


def validate_run(run: dict, current_commit: str) -> None:
    """@description: Refuse stale, fork, non-push or unsuccessful release sources before downloading artifacts.
    @param run: API or workflow_run identity to validate.
    @param current_commit: Fresh main SHA from the owning repository API.
    @return: None; only a completed successful main release is admitted.
    """
    expected = dict(path=WORKFLOW, event='push', head_branch='main', head_sha=current_commit,
                    status='completed', conclusion='success')
    if any(run.get(key) != value for key, value in expected.items()):
        raise ValueError('RELEASE_SOURCE_MISMATCH')
    for key in ('id', 'run_attempt'):
        if type(run.get(key)) is not int or run[key] < 1:
            raise ValueError('RELEASE_ID_INVALID')
    for key in ('repository', 'head_repository'):
        if run.get(key, {}).get('id') != REPOSITORY_ID:
            raise ValueError('RELEASE_REPOSITORY_MISMATCH')


def receipt_artifact(listing: dict, run: dict) -> int:
    """@description: Select exactly one non-expired receipt from the exact successful release attempt.
    @param listing: Artifact listing for this verified run, not a global name search.
    @param run: Independently verified workflow identity.
    @return: Numeric artifact ID; absent, ambiguous or incomplete listings are refused.
    """
    artifacts = listing['artifacts']
    if listing['total_count'] != len(artifacts):
        raise ValueError('RELEASE_ARTIFACT_LIST_INCOMPLETE')
    name = f"release-receipt-{run['id']}-{run['run_attempt']}"
    matches = [item for item in artifacts if item.get('name') == name]
    if len(matches) != 1 or matches[0].get('expired') is not False:
        raise ValueError('RELEASE_RECEIPT_UNAVAILABLE')
    identifier = matches[0]['id']
    if type(identifier) is not int or identifier < 1:
        raise ValueError('RELEASE_ARTIFACT_ID_INVALID')
    return identifier


def decode_receipt(archive: bytes, run: dict) -> dict:
    """@description: Read only the bounded receipt member without extracting an archive to disk.
    @param archive: ZIP downloaded by GitHub from the verified run's artifact ID.
    @param run: Expected source/run/attempt identity.
    @return: Strict deployment request, which still requires host authorization.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        members = bundle.infolist()
        if len(members) != 1 or members[0].filename != 'release.json':
            raise ValueError('RELEASE_ARCHIVE_MEMBERS')
        if members[0].file_size > MAX_RECEIPT_BYTES:
            raise ValueError('RELEASE_RECEIPT_TOO_LARGE')
        receipt = json.loads(bundle.read(members[0]))
    if set(receipt) != {'version', 'commit', 'digest', 'run', 'attempt'} or receipt['version'] != 1:
        raise ValueError('RELEASE_RECEIPT_FORMAT')
    request = {key: receipt[key] for key in ('commit', 'digest', 'run', 'attempt')}
    validated = parse_request(f"deploy-v1 {request['commit']} {request['digest']} {request['run']} {request['attempt']}")
    expected = dict(commit=run['head_sha'], run=run['id'], attempt=run['run_attempt'])
    if validated != request or any(request[key] != value for key, value in expected.items()):
        raise ValueError('RELEASE_RECEIPT_IDENTITY')
    return validated


def resolve(event: dict, api=github_api) -> dict:
    """@description: Recheck the event through the owning API before trusting its artifact identifiers.
    @param event: GitHub workflow_run event document.
    @param api: Network boundary, substituted only in offline tests.
    @return: Four validated public fields for the secret-bearing SSH job.
    """
    current = json.loads(api(f'repos/{REPOSITORY}/branches/main'))['commit']['sha']
    triggered = event['workflow_run']
    validate_run(triggered, current)
    prefix = f"repos/{REPOSITORY}/actions/runs/{triggered['id']}"
    run = json.loads(api(f"{prefix}/attempts/{triggered['run_attempt']}"))
    validate_run(run, current)
    if (run['id'], run['run_attempt']) != (triggered['id'], triggered['run_attempt']):
        raise ValueError('RELEASE_ATTEMPT_MISMATCH')
    listing = json.loads(api(f'{prefix}/artifacts?per_page=100'))
    artifact = receipt_artifact(listing, run)
    return decode_receipt(api(f'repos/{REPOSITORY}/actions/artifacts/{artifact}/zip'), run)


def main() -> None:
    """@description: Pass only strictly validated identifiers to downstream workflow outputs.
    @param: none
    @return: None; a failed lookup or policy check prevents the SSH job from running.
    """
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    request = resolve(event)
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        for key, value in request.items():
            output.write(f'{key}={value}\n')


if __name__ == '__main__':
    main()
