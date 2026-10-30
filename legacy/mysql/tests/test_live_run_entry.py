"""Verify the live-run boundary offline: the token comes only from the hidden reader, the database password only from the in-memory supplier, the
formal switch is explicit, nothing is read from a dotenv file, no command synchronization runs, operator writes reuse exactly the values their
preview printed, and the synthetic candidate enters only through the production pending intake.

Uses the SQL-protocol fake and stand-in callables. No process starts, nothing connects to MySQL or Discord, and no environment file is read.
"""

import pytest

import bot.discord_app as discord_app
from bot.config import load_database_settings
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import PersistenceNotReady
from bot.live_run_entry import (
    LiveRunRefused,
    admit_synthetic_candidate,
    parse_output_field,
    run_owner_sequence,
    run_preview_then_write,
    start_live_entry,
)
from tests.submission_persistence_fake import FakeDatabase

FIXED = {"DISCORD_OWNER_USER_ID": "456708412697215018", "DISCORD_DEV_GUILD_ID": "1559749813011087392",
         "DISCORD_EXPECTED_APPLICATION_ID": "1559751168723521576", "DB_HOST": "127.0.0.1", "DB_PORT": "3307",
         "DB_NAME": "jukebox101_test", "DB_USER": "jukebox101_test_exchange"}
OWNER_ID = 456708412697215018
SYNTHETIC_ID = 9000000000000000001  # [approved candidate]: Used only as a synthetic submitter, never as a DM recipient.
URL = "https://youtu.be/cUfDOS2SINM"


@pytest.fixture
def no_dotenv(monkeypatch):
    """@description: Make any dotenv read fail the test.
    @param monkeypatch: Pytest monkeypatch fixture.
    @return: None.
    """
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: pytest.fail("dotenv must never be read"))


def test_token_comes_from_the_hidden_reader_and_the_password_from_memory_only(no_dotenv, monkeypatch) -> None:
    """@description: The token is requested by name from the hidden reader, the password from the in-memory supplier; the production loader sees both and reads the database settings without a dotenv file.
    @param no_dotenv: Dotenv loading is forbidden.
    @param monkeypatch: Pytest monkeypatch fixture.
    @return: None.
    """
    environ, asked, seen = {}, [], {}
    monkeypatch.setattr("os.environ", environ)

    def reader(name: str) -> str:
        """@description: Return a synthetic token and record which name was asked.
        @param name: Hidden input name.
        @return: Synthetic token value.
        """
        asked.append(name)
        return "synthetic-token"

    def production() -> None:
        """@description: Record the environment the production entry point sees and read the database settings as it does.
        @return: None.
        """
        seen["settings"] = load_database_settings()
        seen["switch"] = environ["JUKEBOX_FORMAL_EXCHANGE"]

    start_live_entry(FIXED, reader, lambda: "synthetic-password", environ, production, formal_exchange=True)
    assert asked == ["DISCORD_BOT_TOKEN"]
    assert environ["DB_PASSWORD"] == "synthetic-password" and environ["DISCORD_BOT_TOKEN"] == "synthetic-token"
    assert seen["switch"] == "1" and seen["settings"].port == 3307 and seen["settings"].database == "jukebox101_test"


@pytest.mark.parametrize(("formal", "expected"), [(False, "0"), (True, "1")])
def test_the_formal_switch_is_set_only_from_the_explicit_argument(no_dotenv, formal: bool, expected: str) -> None:
    """@description: The formal switch follows the explicit argument, so the off start keeps /exchange on the fixed reply.
    @param no_dotenv: Dotenv loading is forbidden.
    @param formal: Explicit switch argument.
    @param expected: Environment value the production entry point must see.
    @return: None.
    """
    environ, seen = {}, {}
    start_live_entry(FIXED, lambda name: "synthetic-token", lambda: "pw", environ, lambda: seen.update(switch=environ["JUKEBOX_FORMAL_EXCHANGE"]),
                     formal_exchange=formal)
    assert seen["switch"] == expected


def test_a_non_boolean_switch_and_an_empty_token_refuse_before_production(no_dotenv) -> None:
    """@description: A non-boolean switch refuses at once, and an empty token refuses before the database password is supplied or production runs.
    @param no_dotenv: Dotenv loading is forbidden.
    @return: None.
    """
    calls = []
    with pytest.raises(LiveRunRefused, match="FORMAL_SWITCH_MUST_BE_EXPLICIT_BOOLEAN"):
        start_live_entry(FIXED, lambda name: "t", lambda: "pw", {}, lambda: calls.append("production"), formal_exchange="on")
    with pytest.raises(LiveRunRefused, match="SECRET_EMPTY:DISCORD_BOT_TOKEN"):
        start_live_entry(FIXED, lambda name: "", lambda: calls.append("password") or "pw", {}, lambda: calls.append("production"),
                         formal_exchange=False)
    assert calls == []


def test_no_command_synchronization_runs_during_the_start(no_dotenv, monkeypatch) -> None:
    """@description: The start never calls command synchronization.
    @param no_dotenv: Dotenv loading is forbidden.
    @param monkeypatch: Pytest monkeypatch fixture.
    @return: None.
    """
    monkeypatch.setattr(discord_app, "synchronize_commands", lambda *a, **k: pytest.fail("no remote sync during the live start"))
    calls = []
    start_live_entry(FIXED, lambda name: "t", lambda: "pw", {}, lambda: calls.append("production"), formal_exchange=False)
    assert calls == ["production"]


def test_a_missing_approved_identifier_refuses_before_any_secret_is_requested() -> None:
    """@description: A missing approved identifier refuses before the token or the password is requested.
    @return: None.
    """
    asked = []
    with pytest.raises(LiveRunRefused, match="FIXED_SETTING_MISSING:DB_PORT"):
        start_live_entry({**FIXED, "DB_PORT": ""}, lambda name: asked.append(name) or "t", lambda: asked.append("pw") or "pw", {},
                         lambda: None, formal_exchange=False)
    assert asked == []


def test_an_operator_write_reuses_exactly_the_digest_and_revision_its_preview_printed() -> None:
    """@description: The write is built from the preview's digest and current revision, not from anything else.
    @return: None.
    """
    calls = []

    def run(argv):
        """@description: Answer the preview with a digest and revision, and the write with success.
        @param argv: Command arguments.
        @return: Exit code and output.
        """
        calls.append(argv)
        if "preview" in argv:
            return 0, "operation: op\ncurrent revision: 7\ndigest: abc123\nwrites: none (preview only)"
        return 0, "review status: approved\nrevision: 8"

    output = run_preview_then_write(run, ["duration", "preview"], lambda revision, digest: ["duration", "write", "--expected-revision", revision, "--digest", digest])
    assert calls[1] == ["duration", "write", "--expected-revision", "7", "--digest", "abc123"]
    assert "revision: 8" in output


def test_a_refused_preview_or_a_missing_digest_stops_before_any_write() -> None:
    """@description: A refused preview, or a preview without a digest, stops the step before the write is attempted.
    @return: None.
    """
    calls = []
    with pytest.raises(LiveRunRefused, match="PREVIEW_REFUSED"):
        run_preview_then_write(lambda argv: (calls.append(argv) or (1, "refused: X")), ["p"], lambda r, d: ["w"])
    with pytest.raises(LiveRunRefused, match="OUTPUT_FIELD_MISSING:digest"):
        run_preview_then_write(lambda argv: (calls.append(argv) or (0, "current revision: 7")), ["p"], lambda r, d: ["w"])
    assert calls == [["p"], ["p"]]
    assert parse_output_field("digest: x", "digest") == "x"


@pytest.fixture
def pending_world():
    """@description: Fake database and real repository with the settlement switch off and readiness flags set.
    @return: Tuple of database and repository.
    """
    database = FakeDatabase()
    database.state["control"].update(snapshot_ready=True, legacy_writers_quiesced=True, settlement_enabled=False)
    return database, SubmissionRepository(database.connect, OWNER_ID)


def test_the_synthetic_candidate_enters_only_through_the_pending_intake_that_creates_its_submitter(pending_world) -> None:
    """@description: The synthetic candidate is admitted by the existing pending intake, which creates the submitter row and the event.
    @param pending_world: Fake database and repository.
    @return: None.
    """
    database, repository = pending_world
    admit_synthetic_candidate(repository, OWNER_ID, SYNTHETIC_ID, URL, "live-c-test")
    assert SYNTHETIC_ID in database.state["users"]
    assert any(sql.startswith("INSERT INTO jb2_events") for sql, _ in database.statements)


def test_the_synthetic_identity_is_never_the_owner_or_a_non_positive_value(pending_world) -> None:
    """@description: The Owner's identifier and non-positive identifiers are refused before the repository is touched.
    @param pending_world: Fake database and repository.
    @return: None.
    """
    database, repository = pending_world
    before = len(database.statements)
    with pytest.raises(LiveRunRefused, match="SYNTHETIC_SUBMITTER_IS_OWNER"):
        admit_synthetic_candidate(repository, OWNER_ID, OWNER_ID, URL, "live-c-2")
    with pytest.raises(LiveRunRefused, match="SYNTHETIC_SUBMITTER_INVALID"):
        admit_synthetic_candidate(repository, OWNER_ID, 0, URL, "live-c-3")
    assert len(database.statements) == before


def test_the_existing_guard_refuses_intake_while_the_settlement_switch_is_on(pending_world) -> None:
    """@description: With the switch on, the production pending intake refuses, so the synthetic candidate cannot enter during a settlement window.
    @param pending_world: Fake database and repository.
    @return: None.
    """
    database, repository = pending_world
    database.state["control"]["settlement_enabled"] = True
    with pytest.raises(PersistenceNotReady):
        admit_synthetic_candidate(repository, OWNER_ID, SYNTHETIC_ID, URL, "live-c-4")
    assert SYNTHETIC_ID not in database.state["users"]


class FakeOwnerCli:
    """@description: Stand-in for the owner command line that prints the same fields and checks each write against its own preview and the current revision.
    @param refuse_write_of: Command kind and action whose write is refused, or None.
    @return: Callable that runs one command like the real CLI.
    """

    def __init__(self, refuse_write_of: str | None = None) -> None:
        """@description: Start both operations at revision 1 and an empty call log.
        @param self: Stand-in.
        @param refuse_write_of: Command kind and action to refuse, or None.
        @return: None.
        """
        import hashlib
        self.hashlib = hashlib
        self.revisions = {"c": 1, "d": 1}
        self.calls = []
        self.refuse = refuse_write_of

    def digest(self, argv: list, revision: str | None) -> str:
        """@description: Bind a digest to the command kind, its operation arguments and the revision it was previewed at.
        @param self: Stand-in.
        @param argv: Command arguments without the revision and digest pairs.
        @param revision: Revision the digest binds to, or None for the settlement window.
        @return: Hexadecimal digest.
        """
        return self.hashlib.sha256("|".join([*argv, str(revision)]).encode()).hexdigest()[:16]

    def __call__(self, argv: list) -> tuple:
        """@description: Answer one preview or write, refusing a write whose digest or revision does not match.
        @param self: Stand-in.
        @param argv: Command arguments.
        @return: Exit code and output.
        """
        kind, action = argv[0], argv[1]
        self.calls.append((kind, action))
        operations = [argv[i + 1] for i, a in enumerate(argv) if a == "--operation"]
        plain = []
        skip = False
        for i, a in enumerate(argv[2:], start=2):
            if skip:
                skip = False
                continue
            if a in ("--expected-revision", "--digest"):
                skip = True
                continue
            plain.append(a)
        if kind == "settle":
            digest = self.digest(plain, None)
            if action == "preview":
                return 0, f"operations: {', '.join(operations)}\nsettlement switch now: False\ndigest: {digest}\nwrites: none (preview only)"
        else:
            label = "c" if operations[0].startswith("live-c") else "d"
            revision = self.revisions[label]
            digest = self.digest(plain, revision)
            if action == "preview":
                return 0, f"current revision: {revision}\ndigest: {digest}\nwrites: none (preview only)"
            if f"{kind} {action}" == self.refuse or argv[argv.index("--digest") + 1] != digest or \
                    argv[argv.index("--expected-revision") + 1] != str(revision):
                return 1, "refused: CONFIRMATION_DIGEST_MISMATCH"
            self.revisions[label] = revision + 1
            return 0, f"review status: confirmed\nrevision: {revision + 1}"
        if kind == "settle" and action == "write":
            if argv[argv.index("--digest") + 1] != digest or self.refuse == "settle write":
                return 1, "refused: CONFIRMATION_DIGEST_MISMATCH"
            return 0, "op-c: awarded\nop-d: awarded\nsettlement switch restored: False"
        return 1, "refused: UNEXPECTED"


OPS = {"c": "live-c-20261025-01", "d": "discord:1000000000000000001"}
DECISIONS = {"duration-c": "dur-c-20261025-01", "duration-d": "dur-d-20261025-01", "approve-c": "app-c-20261025-01",
             "approve-d": "app-d-20261025-01", "version-c": "ver-c-20261025-01", "version-d": "ver-d-20261025-01"}
REASONS = {key: f"Owner 人工確認（測試文字） {key}" for key in DECISIONS}


def test_the_owner_sequence_runs_in_order_and_binds_each_write_to_its_own_preview() -> None:
    """@description: Duration, approval and version for both songs run in order, every write presents its preview's digest and revision, and the settlement window runs last.
    @return: None.
    """
    cli = FakeOwnerCli()
    output = run_owner_sequence(cli, OPS, DECISIONS, REASONS)
    assert cli.calls == [("duration", "preview"), ("duration", "write"), ("approve", "preview"), ("approve", "write"),
                         ("version", "preview"), ("version", "write"), ("duration", "preview"), ("duration", "write"),
                         ("approve", "preview"), ("approve", "write"), ("version", "preview"), ("version", "write"),
                         ("settle", "preview"), ("settle", "write")]
    assert "op-c: awarded" in output["settlement_output"]


def test_a_refused_write_stops_the_sequence_before_any_later_step() -> None:
    """@description: A refused approval write stops the sequence, so no later version or settlement step runs.
    @return: None.
    """
    cli = FakeOwnerCli(refuse_write_of="approve write")
    with pytest.raises(LiveRunRefused, match="WRITE_REFUSED"):
        run_owner_sequence(cli, OPS, DECISIONS, REASONS)
    assert ("version", "preview") not in cli.calls and ("settle", "preview") not in cli.calls
