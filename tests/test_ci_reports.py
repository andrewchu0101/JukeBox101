"""Reject missing, skipped or failing structured CI evidence.

No network, Docker daemon or repository credentials are required.
"""

import pytest

from scripts.ci.check_junit import summarize


@pytest.mark.parametrize('cases,minimum', [
    ('', 1), ('<testcase name="one"><skipped/></testcase>', 1),
    ('<testcase name="one"><failure/></testcase>', 1),
    ('<testcase name="one"><error/></testcase>', 1),
    ('<testcase name="one"/><testcase name="one"/>', 1),
    ('<testcase name="one"/>', 2),
])
def test_bad_junit_fails_closed(tmp_path, cases, minimum):
    """@description: Require genuine nonempty all-pass evidence at the reviewed coverage floor.
    @param tmp_path: Report directory.
    @param cases: Structured case outcomes.
    @param minimum: Required non-decreasing coverage floor.
    @return: None.
    """
    report = tmp_path / 'report.xml'
    report.write_text('<testsuites><testsuite>' + cases + '</testsuite></testsuites>')
    with pytest.raises(ValueError):
        summarize(report, minimum)


def test_additional_tests_do_not_need_exact_count_change(tmp_path):
    """@description: Permit increased coverage with zero skips instead of matching '35 passed'.
    @param tmp_path: Report directory.
    @return: None.
    """
    report = tmp_path / 'report.xml'
    report.write_text('<testsuite><testcase name="one"/><testcase name="two"/></testsuite>')
    assert summarize(report, 1)['passed'] == 2
