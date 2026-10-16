"""Check documented source complexity using only Python's standard AST facilities.

These static checks enforce project conventions without adding tools or contacting services.
"""

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROL_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith, ast.Match)


def _implementation_lines(function, source_lines: list[str]) -> int:
    """@description: Count implementation lines while excluding documentation and comments.
    @param function: Parsed function whose implementation is under review.
    @param source_lines: Original module lines preserving comments and physical line boundaries.
    @return: Number of nonblank noncomment implementation lines.
    """
    implementation_start = function.body[0].lineno
    if ast.get_docstring(function) is not None:
        implementation_start = function.body[0].end_lineno + 1
    return sum(bool(line.strip()) and not line.lstrip().startswith("#")
               for line in source_lines[implementation_start - 1:function.end_lineno])


def _control_depth(node, enclosing_depth: int = 0) -> int:
    """@description: Detect excessive control nesting including transaction contexts.
    @param node: AST node whose child control structures are being inspected.
    @param enclosing_depth: Number of active parent control structures.
    @return: Maximum control depth within the node.
    """
    current_depth = enclosing_depth + isinstance(node, CONTROL_NODES)
    return max([current_depth] + [_control_depth(child, current_depth) for child in ast.iter_child_nodes(node)])


def _assert_function_documentation(function, module_path: Path) -> None:
    """@description: Require labeled intent, parameter meaning and return contracts.
    @param function: Function declaration under review.
    @param module_path: File identity for actionable failures.
    @return: None.
    """
    documentation = ast.get_docstring(function) or ""
    assert "@description:" in documentation and "@return:" in documentation, (module_path, function.name)
    parameters = function.args.posonlyargs + function.args.args + function.args.kwonlyargs
    parameters += [parameter for parameter in (function.args.vararg, function.args.kwarg) if parameter]
    for parameter in parameters:
        assert f"@param {parameter.arg}:" in documentation, (module_path, function.name, parameter.arg)
    if not parameters:
        assert "@param: none" in documentation, (module_path, function.name)


def test_application_functions_follow_project_limits():
    """@description: Protect all application functions from undocumented or oversized changes.
    @param: none
    @return: None.
    """
    for module_path in (PROJECT_ROOT / "bot").rglob("*.py"):
        source = module_path.read_text(encoding="utf-8")
        module = ast.parse(source)
        assert ast.get_docstring(module), module_path
        for function in ast.walk(module):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assert _implementation_lines(function, source.splitlines()) <= 25, (module_path, function.name)
            assert _control_depth(function) <= 3, (module_path, function.name)
            _assert_function_documentation(function, module_path)
