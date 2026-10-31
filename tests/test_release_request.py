"""Exercise deployment receipt resolution without credentials or network access.

The archive is real ZIP bytes; GitHub API responses stay synthetic and establish
no cryptographic authority on the production host.
"""

import copy
import io
import json
import zipfile

import pytest

from deploy.receipt import REPOSITORY_ID, WORKFLOW
from deploy.release_request import decode_receipt, receipt_artifact, resolve, validate_run

COMMIT = 'a' * 40
RUN = dict(id=42, run_attempt=2, head_sha=COMMIT, head_branch='main', event='push',
           status='completed', conclusion='success', path=WORKFLOW,
           repository={'id': REPOSITORY_ID}, head_repository={'id': REPOSITORY_ID})
RECEIPT = dict(version=1, commit=COMMIT, digest='sha256:' + 'b' * 64, run=42, attempt=2)


def archive_for(receipt=RECEIPT, name='release.json'):
    """@description: Produce a real bounded artifact ZIP using synthetic receipt identifiers.
    @param receipt: JSON receipt to encode.
    @param name: Archive member, varied to exercise path rejection.
    @return: In-memory archive bytes.
    """
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as bundle:
        bundle.writestr(name, json.dumps(receipt))
    return output.getvalue()


def test_resolution_rechecks_source_and_exact_attempt():
    """@description: Resolve only one receipt from an independently rechecked completed main release.
    @param: none
    @return: None.
    """
    responses = [json.dumps({'commit': {'sha': COMMIT}}).encode(), json.dumps(RUN).encode(),
                 json.dumps(dict(total_count=1, artifacts=[dict(id=99, expired=False,
                                 name='release-receipt-42-2')])).encode(), archive_for()]
    calls = []

    def api(endpoint):
        """@description: Supply ordered fake API responses and record endpoint scope.
        @param endpoint: Constructed GitHub repository endpoint.
        @return: Synthetic response bytes.
        """
        calls.append(endpoint)
        return responses.pop(0)

    request = resolve({'workflow_run': RUN}, api)
    assert request == {key: value for key, value in RECEIPT.items() if key != 'version'}
    assert calls[1].endswith('/42/attempts/2') and calls[-1].endswith('/99/zip')


@pytest.mark.parametrize('field,value', [
    ('head_sha', 'c' * 40), ('head_branch', 'feature'), ('event', 'pull_request'),
    ('conclusion', 'failure'), ('status', 'in_progress'), ('path', '.github/workflows/ci.yml'),
    ('repository', {'id': 99}), ('head_repository', {'id': 99}), ('id', '../42'), ('run_attempt', True),
])
def test_untrusted_run_refused(field, value):
    """@description: Reject sources that cannot be admitted to the privileged deployment job.
    @param field: Identity field to alter.
    @param value: Untrusted value.
    @return: None.
    """
    run = copy.deepcopy(RUN)
    run[field] = value
    with pytest.raises(ValueError):
        validate_run(run, COMMIT)


@pytest.mark.parametrize('field,value', [
    ('commit', 'c' * 40), ('run', 43), ('attempt', 1), ('digest', 'latest'),
    ('version', 2), ('extra', 'injected'), ('digest', 'sha256:' + 'b' * 64 + '\ninjected=true'),
])
def test_receipt_substitution_and_output_injection_refused(field, value):
    """@description: Prevent artifact substitution or injection into workflow outputs and SSH input.
    @param field: Receipt key to change.
    @param value: Mismatching or malformed value.
    @return: None.
    """
    receipt = dict(RECEIPT, **{field: value})
    with pytest.raises(ValueError):
        decode_receipt(archive_for(receipt), RUN)


@pytest.mark.parametrize('name', ['../release.json', 'other.json', '/release.json'])
def test_archive_paths_are_never_extracted(name):
    """@description: Refuse unexpected archive paths even when their content resembles a receipt.
    @param name: Untrusted ZIP member name.
    @return: None.
    """
    with pytest.raises(ValueError):
        decode_receipt(archive_for(name=name), RUN)


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'expired', 'incomplete'])
def test_ambiguous_artifact_is_refused(fault):
    """@description: Refuse missing, overwritten-attempt, expired or incompletely listed receipts.
    @param fault: Artifact-list failure mode.
    @return: None.
    """
    artifact = dict(id=99, expired=False, name='release-receipt-42-2')
    listing = dict(total_count=1, artifacts=[artifact])
    if fault == 'missing':
        artifact['name'] = 'release-receipt-42-1'
    elif fault == 'duplicate':
        listing.update(total_count=2, artifacts=[artifact, dict(artifact)])
    elif fault == 'expired':
        artifact['expired'] = True
    else:
        listing['total_count'] = 100
    with pytest.raises(ValueError):
        receipt_artifact(listing, RUN)
