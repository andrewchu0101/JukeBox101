"""Validate structured pytest evidence instead of matching a console summary.

All selected test cases must pass with zero skips, failures or errors; historical
minimums are floors, so adding tests never requires changing an exact pass count.
"""

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(path: Path, minimum: int) -> dict:
    """@description: Fail closed on skipped, duplicate, absent or failing JUnit cases.
    @param path: JUnit report emitted by the actual pytest invocation.
    @param minimum: Reviewed non-decreasing coverage floor.
    @return: Structured totals; invalid evidence raises ValueError.
    """
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    identities = {(case.get('classname'), case.get('name')) for case in cases}
    totals = {kind: sum(case.find(kind) is not None for case in cases) for kind in ('skipped', 'failure', 'error')}
    totals.update(tests=len(cases), passed=len(cases) - sum(totals.values()), minimum=minimum)
    if len(cases) < minimum or len(identities) != len(cases) or any(totals[kind] for kind in ('skipped', 'failure', 'error')):
        raise ValueError(json.dumps({'result': 'failed', **totals}, sort_keys=True))
    return {'result': 'passed', **totals}


def main() -> None:
    """@description: Make the same evidence policy available to CI and local runs.
    @param: none
    @return: None; invalid evidence produces a nonzero exit.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('--minimum', required=True, type=int)
    arguments = parser.parse_args()
    print(json.dumps(summarize(arguments.report, arguments.minimum), sort_keys=True))


if __name__ == '__main__':
    main()
