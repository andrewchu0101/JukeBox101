"""Verify the read-only `exchange-stuck` operator subcommand offline: it asks the repository as the configured Owner only, prints codes, reasons and ages
without any link, token or balance, accepts no actor argument, and never calls a writing method.

The repository is a recording fake that raises on any attribute other than the one read method, so a write attempt fails the test. No database is opened.
"""

from datetime import datetime, timezone

import pytest

from bot.owner_cli import main

OWNER = 777
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
ENVIRONMENT = {"DISCORD_OWNER_USER_ID": str(OWNER), "DB_HOST": "127.0.0.1", "DB_PORT": "5432", "DB_NAME": "jukebox101_test",
               "DB_USER": "jukebox101_app", "DB_PASSWORD": "synthetic-not-a-secret"}  # [synthetic environment]: Only for the offline factory.
ROWS = [{"operation_code": 41, "requester": 123456, "delivery_state": "uncertain", "reason": "delivery_result_unknown", "waiting_seconds": 3600},
        {"operation_code": 42, "requester": 654321, "delivery_state": "failed", "reason": "refund_pending", "waiting_seconds": 125}]
FORBIDDEN = ("http", "token", "balance", "watch?v=")  # [leak markers]: Never printed.


class ReadOnlyRepository:
    """Answer only owner_stuck_exchanges and record how it was called; any other attribute is a write attempt."""

    def __init__(self, rows: list, failure: Exception | None = None) -> None:
        """@description: Store the canned rows or the failure to raise.
        @param self: Fake repository.
        @param rows: Rows to return.
        @param failure: Exception to raise instead, or None.
        @return: None.
        """
        self.rows, self.failure, self.calls = rows, failure, []

    def owner_stuck_exchanges(self, requester_id: int, now: datetime) -> list:
        """@description: Record the call and answer.
        @param self: Fake repository.
        @param requester_id: Identity asking.
        @param now: Time of the view.
        @return: Canned rows.
        """
        self.calls.append((requester_id, now))
        if self.failure is not None:
            raise self.failure
        return self.rows

    def __getattr__(self, name: str):
        """@description: Fail loudly on any other repository method, because the command must be read-only.
        @param self: Fake repository.
        @param name: Attribute asked for.
        @return: Never returns.
        """
        raise AssertionError(f"exchange-stuck must not touch repository.{name}")


def run(repository, capsys, argv=("exchange-stuck",), environ=None) -> tuple[int, str]:
    """@description: Run the CLI with the fake repository and return its exit code and output.
    @param repository: Fake repository.
    @param capsys: Output capture.
    @param argv: Arguments after the program name.
    @param environ: Environment, or None for the synthetic one.
    @return: Exit code and printed text.
    """
    code = main(list(argv), environ=environ or ENVIRONMENT, repository_factory=lambda settings, owner: repository, clock=lambda: NOW)
    return code, capsys.readouterr().out


def test_it_asks_as_the_configured_owner_with_the_clock_and_prints_each_case(capsys) -> None:
    """@description: The acting identity is the configured Owner and every case's code, reason and waiting time is printed.
    @param capsys: Output capture.
    @return: None.
    """
    repository = ReadOnlyRepository(ROWS)
    code, output = run(repository, capsys)
    assert code == 0 and len(repository.calls) == 1 and repository.calls[0][0] == OWNER
    assert repository.calls[0][1].replace(tzinfo=None) == NOW.replace(tzinfo=None)
    for expected in ("41", "42", "delivery_result_unknown", "refund_pending", "3600", "125"):
        assert expected in output


def test_the_output_has_no_link_token_or_balance(capsys) -> None:
    """@description: Only identifiers, reasons and ages are printed.
    @param capsys: Output capture.
    @return: None.
    """
    _code, output = run(ReadOnlyRepository(ROWS), capsys)
    assert not any(word in output.lower() for word in FORBIDDEN)


def test_no_stuck_case_is_a_normal_successful_answer(capsys) -> None:
    """@description: An empty list exits 0 and says so in some form, without error text.
    @param capsys: Output capture.
    @return: None.
    """
    code, output = run(ReadOnlyRepository([]), capsys)
    assert code == 0 and output.strip() and "failed" not in output and "refused" not in output


def test_a_repository_refusal_exits_nonzero_without_printing_rows(capsys) -> None:
    """@description: A PermissionError from the repository is reported by class name only and exits 1.
    @param capsys: Output capture.
    @return: None.
    """
    code, output = run(ReadOnlyRepository(ROWS, PermissionError("OWNER_ONLY")), capsys)
    assert code == 1 and "41" not in output and "failed" in output


def test_an_actor_argument_is_not_accepted(capsys) -> None:
    """@description: The Owner identity comes only from the configuration, never from an option.
    @param capsys: Output capture.
    @return: None.
    """
    repository = ReadOnlyRepository(ROWS)
    with pytest.raises(SystemExit):
        run(repository, capsys, argv=("exchange-stuck", "--requester", "999"))
    assert repository.calls == []


def test_a_missing_owner_configuration_refuses_before_any_repository_is_built(capsys) -> None:
    """@description: Without the configured Owner the command refuses and builds nothing.
    @param capsys: Output capture.
    @return: None.
    """
    built = []
    environment = {name: value for name, value in ENVIRONMENT.items() if name != "DISCORD_OWNER_USER_ID"}
    code = main(["exchange-stuck"], environ=environment, repository_factory=lambda settings, owner: built.append(1), clock=lambda: NOW)
    assert code == 1 and built == [] and "refused" in capsys.readouterr().out
