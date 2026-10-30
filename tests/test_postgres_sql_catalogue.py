"""Have PostgreSQL itself parse and type-check every constant SQL statement in bot/ without executing it.

Each statement passed as a constant to cursor.execute(...) is PREPAREd by the least-privilege app role on a migrated
disposable database: syntax, table/column existence and parameter typing are checked; nothing runs and nothing is
written. Statements on the compensation tables (intentionally absent; feature off) may only fail as undefined_table.
"""

import ast
import re
from pathlib import Path

import psycopg
import pytest

from bot.database.compensation_schema import COMPENSATION_TABLES
from bot.database.connection import open_connection

pytestmark = pytest.mark.postgres

BOT_ROOT = Path(__file__).resolve().parents[1] / "bot"
MIN_EXPECTED_STATEMENTS = 120  # [extraction guard]: A broken extractor must fail loudly instead of checking nothing.
UTILITY_PREFIXES = ("SET ",)  # [not preparable]: Utility statements are covered by the integration tests that run them.
OFFLINE_DIALECT_MARKER = "sqlite_master"  # [offline branch]: compensation_schema's SQLite-only catalogue reads for the offline fake.


def _constant_text(node: ast.AST) -> str | None:
    """@description: Return the literal text of a string constant or a concatenation of constants.
    @param node: First argument of an execute() call.
    @return: SQL text, or None when any part is not a constant.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _constant_text(node.left), _constant_text(node.right)
        return left + right if left is not None and right is not None else None
    return None


def collect_statements() -> tuple[list[tuple[str, str]], list[str]]:
    """@description: Find every cursor.execute(...) call in bot/ and split constant SQL from dynamic SQL.
    @param: none
    @return: ([(location, sql)], [locations of dynamic statements])
    """
    constant, dynamic = [], []
    for path in sorted(BOT_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute" and node.args:
                location = f"{path.relative_to(BOT_ROOT.parent)}:{node.lineno}"
                text = _constant_text(node.args[0])
                (constant.append((location, text)) if text is not None else dynamic.append(location))
    return constant, dynamic


def _as_prepared(statement: str) -> str:
    """@description: Turn driver placeholders into PostgreSQL positional parameters for PREPARE.
    @param statement: SQL with %s placeholders and %% escapes.
    @return: SQL with $1..$n.
    """
    counter = iter(range(1, 1000))
    prepared = re.sub(r"%s", lambda _: f"${next(counter)}", statement)
    return prepared.replace("%%", "%")


def _is_compensation(statement: str) -> bool:
    """@description: Recognise statements that touch the intentionally absent compensation tables.
    @param statement: SQL text.
    @return: True when a compensation table is referenced.
    """
    return any(re.search(rf"\b{table}\b", statement) for table in COMPENSATION_TABLES)


def test_every_constant_statement_is_valid_postgresql_for_the_app_role(pg_database):
    """@description: PREPARE each constant statement; only compensation statements may fail, and only as undefined_table.
    @param pg_database: Migrated disposable database.
    @return: None.
    """
    statements, dynamic = collect_statements()
    assert len(statements) >= MIN_EXPECTED_STATEMENTS, len(statements)
    failures, compensation_absent, offline_dialect = [], [], []
    connection = open_connection(pg_database.app)
    connection.autocommit = True
    try:
        for location, statement in statements:
            if statement.lstrip().upper().startswith(UTILITY_PREFIXES):
                continue
            if OFFLINE_DIALECT_MARKER in statement:
                offline_dialect.append(location)  # [never on PostgreSQL]: Only reached when the cursor declares the sqlite dialect.
                continue
            try:
                connection.execute(f"PREPARE jb_catalogue_check AS {_as_prepared(statement)}", prepare=False)
                connection.execute("DEALLOCATE jb_catalogue_check", prepare=False)
            except psycopg.errors.UndefinedTable:
                (compensation_absent if _is_compensation(statement) else failures).append(f"{location}: undefined table")
            except psycopg.Error as error:
                failures.append(f"{location}: {type(error).__name__}: {str(error).splitlines()[0]}")
    finally:
        connection.close()
    print(f"statements={len(statements)} dynamic_skipped={len(dynamic)} compensation_absent={len(compensation_absent)} "
          f"offline_dialect={len(offline_dialect)}")
    assert len(offline_dialect) <= 2  # [bounded exception]: Only the two known SQLite catalogue reads.
    assert failures == []
