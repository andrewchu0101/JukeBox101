"""Verify the reviewed Batch B case list cannot grow silently when the whole MySQL test file is selected.

The MySQL module is read as text and parsed with ast; it is never imported or collected, so no database driver, environment file or network is touched.
"""

import ast
from pathlib import Path

MYSQL_TEST_FILE = Path(__file__).resolve().parent / 'test_batch_b_mysql.py'
REVIEWED_WORKFLOW_CASES = ['persistence', 'priority', 'rejected', 'concurrent_accept', 'concurrent_reward',
                           'concurrent_retry', 'lock_timeout', 'owner_review', 'conflict', 'remix_merge']
EXTRA_CASE_SWITCH = 'JUKEBOX_BATCH_B_REEXAM_SETTLEMENT'


def module_tree() -> ast.Module:
    """@description: Parse the MySQL test module without executing it.
    @param: none
    @return: Abstract syntax tree of the module.
    """
    return ast.parse(MYSQL_TEST_FILE.read_text(encoding='utf-8'))


def function_named(tree: ast.Module, name: str) -> ast.FunctionDef:
    """@description: Locate one top-level test function by name.
    @param tree: Parsed module.
    @param name: Function name.
    @return: The function node.
    """
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def scenario_values(function: ast.FunctionDef) -> list:
    """@description: Read the scenario names from the parametrize decorator on a function.
    @param function: Test function node.
    @return: Ordered scenario names listed in the decorator.
    """
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call) and getattr(decorator.func, 'attr', '') == 'parametrize':
            if decorator.args and isinstance(decorator.args[0], ast.Constant) and decorator.args[0].value == 'scenario':
                return [element.value for element in decorator.args[1].elts]
    raise AssertionError('no scenario parametrize on ' + function.name)


def test_the_reviewed_workflow_list_is_exactly_the_ten_original_cases() -> None:
    """@description: Keep the reviewed workflow selection fixed so adding a scenario cannot widen a whole-file run.
    @param: none
    @return: None.
    """
    assert scenario_values(function_named(module_tree(), 'test_real_batch_b_workflow')) == REVIEWED_WORKFLOW_CASES


def test_the_extra_case_is_not_in_the_reviewed_list() -> None:
    """@description: Keep the unreviewed reexamination settlement case out of the shared reviewed parametrization.
    @param: none
    @return: None.
    """
    assert 'reexam_settlement' not in scenario_values(function_named(module_tree(), 'test_real_batch_b_workflow'))


def test_the_extra_case_has_its_own_explicit_opt_in() -> None:
    """@description: Require a separate environment switch so the case runs only when named on purpose.
    @param: none
    @return: None.
    """
    function = function_named(module_tree(), 'test_real_batch_b_reexam_settlement')
    decorator_text = ast.dump(function.decorator_list[0]) if function.decorator_list else ''
    assert 'skipif' in decorator_text and EXTRA_CASE_SWITCH in decorator_text


def test_the_extra_case_is_kept_and_still_dispatches_its_scenario() -> None:
    """@description: Confirm the case is retained rather than deleted, with its scenario still registered.
    @param: none
    @return: None.
    """
    scenarios = (Path(__file__).resolve().parent / 'batch_b_mysql_scenarios.py').read_text(encoding='utf-8')
    assert 'def reexam_settlement(' in scenarios and 'reexam_settlement,' in scenarios
