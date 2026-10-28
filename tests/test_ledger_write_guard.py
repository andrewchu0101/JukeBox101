"""Guard the existing credit ledger write points offline by scanning bot source with ast and checking synthetic ledger snapshots.

Each approved function that changes users.credit_balance must also insert a credit_ledger row in the same function, and the integrity rules must detect rows changed directly in the tables.
No database, socket or Discord connection is used; the approved write points and the Owner command surface are recorded as constants that any change must update and review.
"""

import ast
import re
import shutil
from pathlib import Path

import pytest

from bot.ledger_integrity import (
    RULE_BALANCE_MATCHES_LEDGER,
    RULE_BALANCE_NOT_NEGATIVE,
    STATUS_BLOCKED,
    STATUS_INCONCLUSIVE_EMPTY_SNAPSHOT,
    STATUS_PASS,
    evaluate_ledger_integrity,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOT_PACKAGE = PROJECT_ROOT / "bot"
MODULE_LEVEL_SCOPE = "<module>"  # [scan scope]: SQL text found outside any function is attributed to this name.

# [approved balance write points]: (repo-relative path, enclosing function) pairs allowed to UPDATE users.credit_balance.
APPROVED_BALANCE_WRITE_POINTS = frozenset({
    ("bot/database/reward_settlement.py", "_award"),
    ("bot/database/exchange_reservation.py", "_reserve"),
    ("bot/database/exchange_delivery_store.py", "refund_undelivered"),
    ("bot/database/compensation_store.py", "_refund_original_debit"),
    # [approved Nickel grant]: /claim grants (核准規則 2026-10-28). Each ledger row is written in the same function
    # as the balance update; the route is closed by default (JUKEBOX_NICKEL_CLAIM) and uses one claim transaction.
    ("bot/database/nickel_grant_store.py", "_insert_grant"),
})
# [approved owner commands]: Top-level slash commands and Owner group subcommands; any addition must update this constant and be reviewed.
# [claim is conditional]: "claim" is registered only when JUKEBOX_NICKEL_CLAIM is open (default closed); approved in 核准規則 2026-10-28.
APPROVED_TOP_LEVEL_COMMANDS = frozenset({"share", "exchange", "balance", "history", "daily", "claim"})
APPROVED_OWNER_SUBCOMMANDS = frozenset({"list", "show", "approve", "reject", "confirm_version", "reexamine"})
OWNER_GROUP_NAME = "owner"
TOP_LEVEL_COMMAND_FILE = "bot/discord_app.py"
OWNER_COMMAND_FILE = "bot/discord_owner_review.py"
COMMAND_FACTORY_NAMES = frozenset({"command", "Command", "Group", "__init__"})
FORBIDDEN_MANUAL_NICKEL_TOKENS = ("adjust", "grant", "mint", "issue", "manual", "set_balance", "debit", "refund", "award")

# [balance write pattern]: Matches an UPDATE or INSERT statement that names credit_balance as a written column.
BALANCE_WRITE_PATTERN = re.compile(
    r"\bUPDATE\s+users\s+SET\b[^;]*\bcredit_balance\s*=|\bINSERT\s+INTO\s+users\b[^;]*\bcredit_balance\b",
    re.IGNORECASE | re.DOTALL,
)
# [ledger insert pattern]: Matches the append-only ledger row that must accompany every balance write.
LEDGER_INSERT_PATTERN = re.compile(r"\bINSERT\s+INTO\s+credit_ledger\b", re.IGNORECASE)


class _SqlStringCollector(ast.NodeVisitor):
    """Attribute every string literal in one module to its innermost enclosing function.

    Collaborates with scan_sql_by_scope; holds only the scope stack and the collected text.
    """

    def __init__(self) -> None:
        """@description: Start with no enclosing function so module-level SQL is still attributed.
        @param: none
        @return: None.
        """
        # [scope stack]: Names of the functions currently being walked, innermost last.
        self.scope_stack = []
        # [collected sql]: Mapping from scope name to the string literals found inside it.
        self.sql_by_scope = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """@description: Track the function being walked so its literals are attributed to it.
        @param node: Function definition node from the parsed module.
        @return: None.
        """
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Constant(self, node: ast.Constant) -> None:
        """@description: Record string literals, which carry every SQL statement in this codebase.
        @param node: Constant node from the parsed module.
        @return: None.
        """
        if isinstance(node.value, str):
            scope = self.scope_stack[-1] if self.scope_stack else MODULE_LEVEL_SCOPE
            self.sql_by_scope.setdefault(scope, []).append(node.value)


def scan_sql_by_scope(source_file: Path) -> dict:
    """@description: Expose the SQL text of one module grouped by function so write points can be compared with the approved list.
    @param source_file: Python file under the bot package.
    @return: Mapping from enclosing function name to the string literals inside it.
    """
    collector = _SqlStringCollector()
    collector.visit(ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file)))
    return collector.sql_by_scope


def find_balance_write_points(package_root: Path) -> frozenset:
    """@description: Locate every function that changes users.credit_balance so unapproved writers fail the guard.
    @param package_root: Directory of the bot package to scan.
    @return: Frozen set of (repo-relative path, function name) pairs containing a balance write.
    """
    write_points = set()
    for source_file in sorted(package_root.rglob("*.py")):
        relative_path = source_file.relative_to(package_root.parent).as_posix()
        for scope, statements in scan_sql_by_scope(source_file).items():
            if any(BALANCE_WRITE_PATTERN.search(statement) for statement in statements):
                write_points.add((relative_path, scope))
    return frozenset(write_points)


def find_unpaired_write_points(package_root: Path) -> frozenset:
    """@description: Locate balance writes whose function lacks the matching credit_ledger insert, which would break the ledger pair.
    @param package_root: Directory of the bot package to scan.
    @return: Frozen set of (repo-relative path, function name) pairs with a balance write and no ledger insert.
    """
    unpaired = set()
    for source_file in sorted(package_root.rglob("*.py")):
        relative_path = source_file.relative_to(package_root.parent).as_posix()
        for scope, statements in scan_sql_by_scope(source_file).items():
            if _is_unpaired_balance_write(statements):
                unpaired.add((relative_path, scope))
    return frozenset(unpaired)


def _is_unpaired_balance_write(statements: list) -> bool:
    """@description: Decide whether one function changes the balance without also appending its ledger row.
    @param statements: SQL string literals found inside the function.
    @return: True when a balance write exists and no credit_ledger insert accompanies it.
    """
    has_balance_write = any(BALANCE_WRITE_PATTERN.search(statement) for statement in statements)
    has_ledger_insert = any(LEDGER_INSERT_PATTERN.search(statement) for statement in statements)
    return has_balance_write and not has_ledger_insert


def collect_command_names(relative_path: str) -> frozenset:
    """@description: Enumerate the slash command and group names one module registers so a manual Nickel command cannot appear unnoticed.
    @param relative_path: Repo-relative module that registers Discord commands.
    @return: Frozen set of every name passed to a command factory in that module.
    """
    tree = ast.parse((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"), filename=relative_path)
    command_names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _factory_name(node) not in COMMAND_FACTORY_NAMES:
            continue
        literal_name = _name_keyword(node)
        if literal_name is not None:
            command_names.add(literal_name)
    return frozenset(command_names)


def _factory_name(call: ast.Call) -> str:
    """@description: Return the bare name of the called factory so attribute and plain calls are treated alike.
    @param call: Call node whose target is inspected.
    @return: Attribute or identifier name of the call target, or an empty string.
    """
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return getattr(call.func, "id", "")


def _name_keyword(call: ast.Call):
    """@description: Extract the literal name keyword of a command call, which is the only identifier Discord shows.
    @param call: Command factory call node.
    @return: The literal name string, or None when the call has no literal name.
    """
    for keyword in call.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def copy_bot_package(destination: Path) -> Path:
    """@description: Create an isolated copy of the bot package so mutation checks never touch the real source.
    @param destination: Empty directory that will receive the copy.
    @return: Path of the copied bot package.
    """
    copied_package = destination / "bot"
    shutil.copytree(BOT_PACKAGE, copied_package, ignore=shutil.ignore_patterns("__pycache__"))
    return copied_package


def _user_row(discord_user_id: int, credit_balance: int) -> dict:
    """@description: Build one users snapshot row with the columns the integrity rules read.
    @param discord_user_id: Synthetic account identifier.
    @param credit_balance: Balance stored in the users cache.
    @return: Row mapping.
    """
    return {"discord_user_id": discord_user_id, "credit_balance": credit_balance}


def _ledger_row(ledger_id: int, discord_user_id: int, credit_delta: int) -> dict:
    """@description: Build one credit_ledger snapshot row with the columns the integrity rules read.
    @param ledger_id: Synthetic ledger identifier.
    @param discord_user_id: Synthetic account identifier the ledger entry belongs to.
    @param credit_delta: Signed change recorded by the entry.
    @return: Row mapping.
    """
    return {"ledger_id": ledger_id, "discord_user_id": discord_user_id, "credit_delta": credit_delta, "reason": "delivery_refund"}


def _consistent_snapshot() -> tuple:
    """@description: Provide a snapshot where every balance equals its ledger sum, the baseline the mutations break.
    @param: none
    @return: Tuple of user, ledger and claim row lists.
    """
    user_rows = [_user_row(900000000000000201, 2), _user_row(900000000000000202, 0)]
    ledger_rows = [_ledger_row(1, 900000000000000201, 1), _ledger_row(2, 900000000000000201, 1), _ledger_row(3, 900000000000000202, 0)]
    return user_rows, ledger_rows, []


def test_balance_write_points_match_approved_list() -> None:
    """@description: Fail when any function other than the approved ones changes users.credit_balance.
    @param: none
    @return: None.
    """
    assert find_balance_write_points(BOT_PACKAGE) == APPROVED_BALANCE_WRITE_POINTS


def test_every_approved_write_point_appends_ledger_row() -> None:
    """@description: Confirm each approved balance write appends a credit_ledger row in the same function, keeping cache and ledger paired.
    @param: none
    @return: None.
    """
    assert find_unpaired_write_points(BOT_PACKAGE) == frozenset()
    assert find_balance_write_points(BOT_PACKAGE) == APPROVED_BALANCE_WRITE_POINTS


def test_guard_detects_added_unapproved_balance_writer(tmp_path: Path) -> None:
    """@description: Prove the scanner catches a new writer by mutating an isolated copy of the package.
    @param tmp_path: Pytest directory for the isolated copy.
    @return: None.
    """
    copied_package = copy_bot_package(tmp_path)
    (copied_package / "database" / "manual_adjustment.py").write_text(
        "def apply_manual_adjustment(cursor, discord_user_id):\n"
        "    cursor.execute(\"INSERT INTO credit_ledger (discord_user_id,credit_delta,reason,idempotency_key) VALUES (%s,5,'manual',%s)\", (discord_user_id, 'x'))\n"
        "    cursor.execute(\"UPDATE users SET credit_balance=credit_balance+5 WHERE discord_user_id=%s\", (discord_user_id,))\n",
        encoding="utf-8",
    )
    unapproved = find_balance_write_points(copied_package) - APPROVED_BALANCE_WRITE_POINTS
    assert unapproved == {("bot/database/manual_adjustment.py", "apply_manual_adjustment")}


def test_guard_detects_balance_write_without_ledger_row(tmp_path: Path) -> None:
    """@description: Prove a balance write that skips the ledger append is reported as unpaired.
    @param tmp_path: Pytest directory for the isolated copy.
    @return: None.
    """
    copied_package = copy_bot_package(tmp_path)
    (copied_package / "database" / "silent_credit.py").write_text(
        "def silently_credit(cursor, discord_user_id):\n"
        "    cursor.execute(\"UPDATE users SET credit_balance = credit_balance + 1 WHERE discord_user_id = %s\", (discord_user_id,))\n",
        encoding="utf-8",
    )
    assert find_unpaired_write_points(copied_package) == {("bot/database/silent_credit.py", "silently_credit")}


def test_owner_commands_are_exactly_the_approved_surface() -> None:
    """@description: Fail when a command or Owner subcommand is added, so any new money-affecting command needs review.
    @param: none
    @return: None.
    """
    assert collect_command_names(TOP_LEVEL_COMMAND_FILE) == APPROVED_TOP_LEVEL_COMMANDS
    assert collect_command_names(OWNER_COMMAND_FILE) == APPROVED_OWNER_SUBCOMMANDS | {OWNER_GROUP_NAME}


def test_no_registered_command_adjusts_nickel() -> None:
    """@description: Confirm no registered command name suggests a manual Nickel grant, adjustment or debit.
    @param: none
    @return: None.
    """
    all_names = collect_command_names(TOP_LEVEL_COMMAND_FILE) | collect_command_names(OWNER_COMMAND_FILE)
    offending = sorted(name for name in all_names if any(token in name for token in FORBIDDEN_MANUAL_NICKEL_TOKENS))
    assert offending == []


def test_integrity_passes_consistent_snapshot() -> None:
    """@description: Confirm a snapshot whose balances equal their ledger sums produces no findings.
    @param: none
    @return: None.
    """
    verdict = evaluate_ledger_integrity(*_consistent_snapshot())
    assert verdict.status == STATUS_PASS
    assert verdict.findings == ()


def test_integrity_detects_direct_balance_update() -> None:
    """@description: Detect a balance raised directly in users without a ledger row, which is the failure the guard exists to catch.
    @param: none
    @return: None.
    """
    user_rows, ledger_rows, claim_rows = _consistent_snapshot()
    user_rows[1]["credit_balance"] = 1
    verdict = evaluate_ledger_integrity(user_rows, ledger_rows, claim_rows)
    assert verdict.status == STATUS_BLOCKED
    assert verdict.blocks_beta_activation is True
    assert verdict.finding_counts[RULE_BALANCE_MATCHES_LEDGER] == 1


def test_integrity_detects_deleted_ledger_row() -> None:
    """@description: Detect a ledger entry removed directly from the table while the cached balance remains.
    @param: none
    @return: None.
    """
    user_rows, ledger_rows, claim_rows = _consistent_snapshot()
    verdict = evaluate_ledger_integrity(user_rows, ledger_rows[1:], claim_rows)
    assert verdict.status == STATUS_BLOCKED
    assert verdict.finding_counts[RULE_BALANCE_MATCHES_LEDGER] == 1


def test_integrity_detects_negative_cached_balance() -> None:
    """@description: Detect a balance below the schema floor, which a direct table edit could create.
    @param: none
    @return: None.
    """
    user_rows, ledger_rows, claim_rows = _consistent_snapshot()
    user_rows[1]["credit_balance"] = -1
    verdict = evaluate_ledger_integrity(user_rows, ledger_rows, claim_rows)
    assert verdict.status == STATUS_BLOCKED
    assert verdict.finding_counts[RULE_BALANCE_NOT_NEGATIVE] == 1


def test_integrity_empty_users_snapshot_is_inconclusive() -> None:
    """@description: Refuse to report PASS for an empty users snapshot, so an unreadable table cannot look healthy.
    @param: none
    @return: None.
    """
    verdict = evaluate_ledger_integrity([], [], [])
    assert verdict.status == STATUS_INCONCLUSIVE_EMPTY_SNAPSHOT
    assert verdict.blocks_beta_activation is True
