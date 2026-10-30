"""Run regression tests with no container network and extract structured evidence.

Creates and removes only this invocation's random test container; no Docker prune
or production network/volume/secret is used.
"""

import os
import secrets
import subprocess
from pathlib import Path

from scripts.ci.check_junit import summarize


def main() -> None:
    """@description: Require the historical coverage floor and zero unexpected skips.
    @param: none
    @return: None; Docker or test/report errors fail CI.
    """
    name = 'jb-offline-' + secrets.token_hex(8)
    output = Path('ci-evidence')
    output.mkdir(exist_ok=True)
    image = os.environ.get('TEST_IMAGE', 'jukebox101-test:v7-local')
    subprocess.run(['docker', 'create', '--name', name, '--network', 'none', image,
                    '-q', '-p', 'no:cacheprovider', '-m', 'not postgres', '--junitxml=/tmp/offline.xml'], check=True)
    try:
        result = subprocess.run(['docker', 'start', '-a', name], timeout=900)
        subprocess.run(['docker', 'cp', f'{name}:/tmp/offline.xml', str(output / 'offline.xml')], check=True)
        print(summarize(output / 'offline.xml', 1747), flush=True)
        result.check_returncode()
        status = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.ExitCode}}', name], text=True)
        if status.strip() != '0':
            raise RuntimeError('offline test process failed')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=True)


if __name__ == '__main__':
    main()
