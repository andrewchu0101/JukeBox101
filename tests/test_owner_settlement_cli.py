"""Verify the operator CLI for Owner-confirmed versions and the named settlement window offline: previews write nothing, digests bind the
exact inputs, the switch opens only around the named settle calls and is restored on success and failure alike, and a failed restore is reported.

Runs over the real repository and owner service on the SQL-protocol fake. Nothing connects to MySQL, Discord or the environment files.
"""

import pytest

from bot.owner_cli import main
from bot.version_identity import VersionIdentityStatus
from tests.intake_fixture import seed_intake
from tests.test_identity_service_path import (  # noqa: F401  (world is a pytest fixture imported by name)
    OWNER_ID,
    START,
    confirm_version,
    approve,
    owner_evidence,
    submission,
    video_of,
    world,
)

ENVIRONMENT = {"DISCORD_OWNER_USER_ID": str(OWNER_ID), "DB_HOST": "127.0.0.1", "DB_PORT": "3307", "DB_NAME": "jukebox101_test",
               "DB_USER": "jukebox101_test_exchange", "DB_PASSWORD": "synthetic-not-a-secret"}  # [synthetic environment]: Offline only.
VERSION_NAME = "orig-A"
SOURCE = "https://example.com/synthetic-version-note"


def run(argv, capsys, factory, environ=ENVIRONMENT) -> tuple[int, str]:
    """@description: Run the CLI with an injected environment and repository, returning the exit code and everything it printed.
    @param argv: Arguments after the program name.
    @param capsys: Pytest capture fixture.
    @param factory: Repository factory standing in for the database connection.
    @param environ: Environment mapping the CLI may read.
    @return: Exit code and captured standard output.
    """
    code = main(argv, environ=environ, repository_factory=factory, clock=lambda: START)
    return code, capsys.readouterr().out


def digest_of(output: str) -> str:
    """@description: Extract the digest line a preview printed, so a write can present exactly that value.
    @param output: Captured preview output.
    @return: Digest text.
    """
    return next(line.split(": ", 1)[1] for line in output.splitlines() if line.startswith("digest: "))


@pytest.fixture
def pending_operation(world):
    """@description: One accepted, unconfirmed synthetic submission whose version and approval are not yet set.
    @param world: Offline database, repository and service.
    @return: Tuple of database, repository and service.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False  # [payout switch off]: The window tests open it themselves.
    seed_intake(repository, submission(1))
    return database, repository, service


@pytest.fixture
def approved_operation(world):
    """@description: One synthetic submission with its version confirmed and approved, ready for a settlement window.
    @param world: Offline database, repository and service.
    @return: Tuple of database, repository and service.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False  # [payout switch off]: The window tests open it themselves.
    seed_intake(repository, submission(1))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-1", VERSION_NAME))
    approve(service, 1)
    return database, repository, service


def identity_status(repository) -> VersionIdentityStatus:
    """@description: Read the stored identity status of the synthetic operation through the repository.
    @param repository: Repository over the fake.
    @return: Identity status as stored.
    """
    return repository.review_view(OWNER_ID, "op-1").identity.status


def version_arguments(action: str, *extra: str, name: str = VERSION_NAME, source: str = SOURCE) -> list:
    """@description: Build the version subcommand arguments for one action.
    @param action: preview or write.
    @param extra: Additional arguments, such as the digest for a write.
    @param name: Canonical version reference.
    @param source: Traceable source of the evidence.
    @return: Argument list.
    """
    return ["version", action, "--operation", "op-1", "--expected-revision", "1", "--version-name", name, "--version-kind", "basic",
            "--source", source, "--reason", "synthetic version note", *extra]


def test_a_version_preview_writes_nothing_and_prints_the_digest_its_write_accepts(pending_operation, capsys) -> None:
    """@description: The preview reports the projected identity and a digest, and leaves the stored identity unresolved.
    @param pending_operation: One accepted synthetic submission.
    @return: None.
    """
    _, repository, _ = pending_operation
    code, output = run(version_arguments("preview"), capsys, lambda s, o: repository)
    assert code == 0 and "writes: none (preview only)" in output
    assert identity_status(repository) is VersionIdentityStatus.UNRESOLVED


def test_a_version_write_with_the_previewed_digest_confirms_the_identity(pending_operation, capsys) -> None:
    """@description: A write presenting the previewed digest confirms the version through the owner service, and nothing else changes.
    @param pending_operation: One accepted synthetic submission.
    @return: None.
    """
    _, repository, _ = pending_operation
    _, preview_output = run(version_arguments("preview"), capsys, lambda s, o: repository)
    code, _ = run(version_arguments("write", "--digest", digest_of(preview_output)), capsys, lambda s, o: repository)
    assert code == 0
    assert identity_status(repository) is VersionIdentityStatus.CONFIRMED


def test_a_changed_version_name_is_refused_by_the_digest_and_writes_nothing(pending_operation, capsys) -> None:
    """@description: A write whose version name differs from the previewed one is refused before any service call.
    @param pending_operation: One accepted synthetic submission.
    @return: None.
    """
    _, repository, _ = pending_operation
    _, preview_output = run(version_arguments("preview"), capsys, lambda s, o: repository)
    code, output = run(version_arguments("write", "--digest", digest_of(preview_output), name="orig-B"), capsys,
                       lambda s, o: repository)
    assert code == 1 and "CONFIRMATION_DIGEST_MISMATCH" in output
    assert identity_status(repository) is VersionIdentityStatus.UNRESOLVED


def test_a_source_that_is_not_traceable_is_refused_before_any_read(pending_operation, capsys) -> None:
    """@description: A bare numeric source is refused as not traceable, with no preview token and no write.
    @param pending_operation: One accepted synthetic submission.
    @return: None.
    """
    _, repository, _ = pending_operation
    code, output = run(version_arguments("preview", source="123456789"), capsys, lambda s, o: repository)
    assert code == 1 and "SOURCE_NOT_TRACEABLE" in output
    assert identity_status(repository) is VersionIdentityStatus.UNRESOLVED


def test_a_non_owner_configured_identity_writes_no_version(pending_operation, capsys) -> None:
    """@description: An environment naming another identity is refused by the repository and the stored identity stays unchanged.
    @param pending_operation: One accepted synthetic submission.
    @return: None.
    """
    _, repository, _ = pending_operation
    other = {**ENVIRONMENT, "DISCORD_OWNER_USER_ID": str(OWNER_ID + 1)}
    code, _ = run(version_arguments("preview"), capsys, lambda s, o: repository, environ=other)
    assert code == 1
    assert identity_status(repository) is VersionIdentityStatus.UNRESOLVED


def test_a_settlement_preview_writes_nothing_and_prints_the_switch_and_digest(approved_operation, capsys) -> None:
    """@description: The settlement preview reads the switch and the operation list and changes neither.
    @param approved_operation: One approved synthetic submission.
    @return: None.
    """
    database, repository, _ = approved_operation
    code, output = run(["settle", "preview", "--operation", "op-1"], capsys, lambda s, o: repository)
    assert code == 0 and "settlement switch now: False" in output and "digest:" in output
    assert database.state["control"]["settlement_enabled"] is False and database.state["credit_ledger"] == {}


def test_a_settlement_write_opens_the_switch_only_around_the_named_settle_and_restores_it(approved_operation, capsys) -> None:
    """@description: The switch is on exactly while the named settle runs, is off again afterwards, and the award is recorded.
    @param approved_operation: One approved synthetic submission.
    @param capsys: Pytest capture fixture.
    @return: None.
    """
    database, repository, _ = approved_operation
    _, preview_output = run(["settle", "preview", "--operation", "op-1"], capsys, lambda s, o: repository)
    seen_during_settle = []
    real_settle = repository.settle

    def recording_settle(operation_id):
        """@description: Record the switch value at the moment each settle runs, then delegate to the real call.
        @param operation_id: Operation being settled.
        @return: Real settlement decision.
        """
        seen_during_settle.append(database.state["control"]["settlement_enabled"])
        return real_settle(operation_id)

    repository.settle = recording_settle
    code, output = run(["settle", "write", "--operation", "op-1", "--digest", digest_of(preview_output)], capsys,
                       lambda s, o: repository)
    assert code == 0 and "op-1: awarded" in output and "settlement switch restored: False" in output
    assert seen_during_settle == [True]
    assert database.state["control"]["settlement_enabled"] is False
    assert len(database.state["credit_ledger"]) >= 1


def test_a_settlement_window_refuses_when_the_switch_is_already_on(approved_operation, capsys) -> None:
    """@description: A window never opens over a switch that is already on, so nothing is settled and the value is untouched.
    @param approved_operation: One approved synthetic submission.
    @param capsys: Pytest capture fixture.
    @return: None.
    """
    database, repository, _ = approved_operation
    database.state["control"]["settlement_enabled"] = True
    _, preview_output = run(["settle", "preview", "--operation", "op-1"], capsys, lambda s, o: repository)
    code, output = run(["settle", "write", "--operation", "op-1", "--digest", digest_of(preview_output)], capsys,
                       lambda s, o: repository)
    assert code == 1 and "SETTLEMENT_SWITCH_MUST_BE_OFF_BEFORE_WINDOW" in output
    assert database.state["control"]["settlement_enabled"] is True and database.state["credit_ledger"] == {}


def test_a_settlement_digest_mismatch_settles_nothing(approved_operation, capsys) -> None:
    """@description: A write with a digest for another operation list is refused before the switch moves.
    @param approved_operation: One approved synthetic submission.
    @param capsys: Pytest capture fixture.
    @return: None.
    """
    database, repository, _ = approved_operation
    code, output = run(["settle", "write", "--operation", "op-1", "--digest", "0" * 64], capsys, lambda s, o: repository)
    assert code == 1 and "CONFIRMATION_DIGEST_MISMATCH" in output
    assert database.state["control"]["settlement_enabled"] is False and database.state["credit_ledger"] == {}


def test_a_failing_settle_still_restores_the_switch_and_says_so(approved_operation, capsys, monkeypatch) -> None:
    """@description: An exception during the window is reported by class name, and the switch is restored to off before the command returns.
    @param approved_operation: One approved synthetic submission.
    @param capsys: Pytest capture fixture.
    @param monkeypatch: Pytest monkeypatch fixture.
    @return: None.
    """
    database, repository, _ = approved_operation
    _, preview_output = run(["settle", "preview", "--operation", "op-1"], capsys, lambda s, o: repository)

    def failing_settle(operation_id):
        """@description: Simulate a settlement failure inside the window.
        @param operation_id: Operation being settled.
        @return: Never returns.
        """
        raise RuntimeError("synthetic settlement failure")

    monkeypatch.setattr(repository, "settle", failing_settle)
    code, output = run(["settle", "write", "--operation", "op-1", "--digest", digest_of(preview_output)], capsys,
                       lambda s, o: repository)
    assert code == 1 and "failed: RuntimeError" in output and "settlement switch restored: False" in output
    assert database.state["control"]["settlement_enabled"] is False


def test_a_failed_restore_is_reported_on_its_own_line_and_is_never_hidden(approved_operation, capsys, monkeypatch) -> None:
    """@description: If the switch cannot be restored, the command says so explicitly and exits non-zero instead of reporting success.
    @param approved_operation: One approved synthetic submission.
    @param capsys: Pytest capture fixture.
    @param monkeypatch: Pytest monkeypatch fixture.
    @return: None.
    """
    database, repository, _ = approved_operation
    _, preview_output = run(["settle", "preview", "--operation", "op-1"], capsys, lambda s, o: repository)
    real_set = repository.set_settlement_enabled
    calls = []

    def flaky_set(requester_id, enabled):
        """@description: Succeed when opening the window, then fail on the restore call.
        @param requester_id: Acting Owner.
        @param enabled: Requested switch value.
        @return: Value read back by the real call on the first call.
        """
        calls.append(enabled)
        if len(calls) == 2:
            raise ConnectionError("synthetic restore failure")
        return real_set(requester_id, enabled)

    monkeypatch.setattr(repository, "set_settlement_enabled", flaky_set)
    code, output = run(["settle", "write", "--operation", "op-1", "--digest", digest_of(preview_output)], capsys,
                       lambda s, o: repository)
    assert code == 1 and "RESTORE FAILED (ConnectionError)" in output
    assert "settlement switch restored" not in output
    assert calls == [True, False]


def test_settlement_operations_must_be_distinct_and_present(approved_operation, capsys) -> None:
    """@description: A duplicated operation identifier is refused, so one window cannot settle the same operation twice by accident.
    @param approved_operation: One approved synthetic submission.
    @param capsys: Pytest capture fixture.
    @return: None.
    """
    _, repository, _ = approved_operation
    code, output = run(["settle", "preview", "--operation", "op-1", "--operation", "op-1"], capsys, lambda s, o: repository)
    assert code == 1 and "SETTLEMENT_OPERATIONS_INVALID" in output


def test_the_settlement_switch_needs_its_previewed_digest_and_reads_back(approved_operation, capsys) -> None:
    """@description: Entering automatic mode is an explicit Owner step: a preview writes nothing, a wrong digest refuses, the right one writes and reads back.
    @param approved_operation: Offline world with an approved operation.
    @param capsys: Output capture.
    @return: None.
    """
    _database, repository, _service = approved_operation
    factory = lambda _settings, _owner: repository  # noqa: E731
    code, output = run(["settlement-switch", "preview", "--enabled", "on"], capsys, factory)
    assert code == 0 and "writes: none" in output and repository.settlement_enabled(OWNER_ID) is False
    code, output_refused = run(["settlement-switch", "write", "--enabled", "on", "--digest", "0" * 64], capsys, factory)
    assert code == 1 and repository.settlement_enabled(OWNER_ID) is False
    code, output_written = run(["settlement-switch", "write", "--enabled", "on", "--digest", digest_of(output)], capsys, factory)
    assert code == 0 and "True" in output_written and repository.settlement_enabled(OWNER_ID) is True
