"""Offline contract of the retention switch, the pipeline's use of it, and the operator command.

Behaviour against real tables is covered by test_postgres_api_retention.py; these tests pin what must hold without a database.
"""

import pytest

from bot.api_retention import main
from bot.auto_pipeline import AutoPipeline
from bot.auto_startup import build_auto_pipeline, load_auto_settings
from bot.config import ConfigurationError
from bot.database.api_observations import RetentionStatus

OWNER = "900001"
DATABASE_ENVIRONMENT = {"DISCORD_OWNER_USER_ID": OWNER, "DB_HOST": "h", "DB_PORT": "5432", "DB_NAME": "n", "DB_USER": "app", "DB_PASSWORD": "p"}


class RecordingRepository:
    """Record every call the pipeline makes, answering the retention queries from a script."""

    def __init__(self, oldest_overdue_days=None, purge_error: Exception | None = None) -> None:
        """@description: Start with no calls.
        @param self: Fake repository.
        @param oldest_overdue_days: Debt the status reports, or None.
        @param purge_error: Failure raised by the purge, or None.
        @return: None.
        """
        self.calls: list[str] = []
        self.oldest_overdue_days = oldest_overdue_days
        self.purge_error = purge_error

    def pending_validation_operations(self, limit, excluded=()):
        """@description: Report nothing pending.
        @param self: Fake repository.
        @param limit: Unused.
        @param excluded: Unused.
        @return: Empty list.
        """
        self.calls.append("pending_validation_operations")
        return []

    def ready_operations(self):
        """@description: Report nothing ready.
        @param self: Fake repository.
        @return: Empty list.
        """
        self.calls.append("ready_operations")
        return []

    def revalidation_operations(self, limit, excluded=()):
        """@description: Report nothing to revalidate.
        @param self: Fake repository.
        @param limit: Unused.
        @param excluded: Unused.
        @return: Empty list.
        """
        self.calls.append("revalidation_operations")
        return []

    def purge_api_observations(self):
        """@description: Purge, or fail as scripted.
        @param self: Fake repository.
        @return: One row cleared.
        """
        self.calls.append("purge_api_observations")
        if self.purge_error is not None:
            raise self.purge_error
        return 1

    def api_retention_status(self):
        """@description: Report the scripted debt.
        @param self: Fake repository.
        @return: Status.
        """
        self.calls.append("api_retention_status")
        return RetentionStatus(1, 1, self.oldest_overdue_days, None)

    def api_purge_preview(self):
        """@description: Report what a purge would clear.
        @param self: Fake repository.
        @return: Counts.
        """
        self.calls.append("api_purge_preview")
        return 2, 1


def pipeline(repository, retention: bool, clock=lambda: 0.0) -> AutoPipeline:
    """@description: Build a pipeline without a provider, so only the retention behaviour is exercised.
    @param repository: Fake repository.
    @param retention: Retention switch.
    @param clock: Monotonic clock.
    @return: Pipeline.
    """
    return AutoPipeline(repository, None, frozenset(), settlement_enabled=False, api_retention=retention, monotonic=clock)


def test_the_switch_is_closed_by_default_and_refuses_unknown_values():
    """@description: Retention follows the same strict on/off rule as the other modes.
    @param: none
    @return: None.
    """
    assert load_auto_settings({}).api_retention is False
    assert load_auto_settings({"JUKEBOX_API_RETENTION": "on"}).api_retention is True
    with pytest.raises(ConfigurationError):
        load_auto_settings({"JUKEBOX_API_RETENTION": "maybe"})


def test_retention_alone_builds_a_pipeline_so_the_purge_runs_with_review_switched_off():
    """@description: Switching automatic review off to stop API use must not stop the purge.
    @param: none
    @return: None.
    """
    assert build_auto_pipeline(RecordingRepository(), load_auto_settings({})) is None
    assert build_auto_pipeline(RecordingRepository(), load_auto_settings({"JUKEBOX_API_RETENTION": "on"})) is not None


def test_with_the_switch_off_the_pipeline_never_touches_the_retention_tables():
    """@description: The previous behaviour is unchanged: no retention query is made when the switch is off.
    @param: none
    @return: None.
    """
    repository = RecordingRepository()
    pipeline(repository, False).sweep()
    pipeline(repository, False).maintain_retention()
    assert not {"purge_api_observations", "api_retention_status", "revalidation_operations"} & set(repository.calls)


def test_a_sweep_purges_first_and_then_only_once_a_day():
    """@description: The first sweep purges before anything else; the next purge waits a day, and a failed purge is retried at the next sweep.
    @param: none
    @return: None.
    """
    now = [0.0]
    repository = RecordingRepository()
    running = pipeline(repository, True, lambda: now[0])
    running.sweep()
    assert repository.calls.index("purge_api_observations") < repository.calls.index("pending_validation_operations")
    now[0] = 3600.0
    running.sweep()
    assert repository.calls.count("purge_api_observations") == 1
    now[0] = 86401.0
    running.sweep()
    assert repository.calls.count("purge_api_observations") == 2
    failing = RecordingRepository(purge_error=ValueError("x"))
    retried = pipeline(failing, True, lambda: now[0])
    retried.sweep()
    retried.sweep()
    assert failing.calls.count("purge_api_observations") == 2


def test_an_unreadable_status_stops_new_lookups_rather_than_assuming_health():
    """@description: If the debt cannot be read, no new official value is fetched.
    @param: none
    @return: None.
    """
    repository = RecordingRepository()
    repository.api_retention_status = lambda: (_ for _ in ()).throw(ValueError("down"))
    running = pipeline(repository, True)
    running.maintain_retention()
    assert running._lookups_stopped is True


def test_the_command_previews_by_default_and_clears_only_with_apply(capsys):
    """@description: purge without --apply reports counts and writes nothing; with --apply it clears; status never writes.
    @param capsys: Output capture.
    @return: None.
    """
    repository = RecordingRepository()
    factory = lambda settings, owner: repository
    assert main(["purge"], environ=DATABASE_ENVIRONMENT, repository_factory=factory) == 0
    assert "preview only" in capsys.readouterr().out and "purge_api_observations" not in repository.calls
    assert main(["purge", "--apply"], environ=DATABASE_ENVIRONMENT, repository_factory=factory) == 0
    assert "cleared: 1" in capsys.readouterr().out and "purge_api_observations" in repository.calls
    assert main(["status"], environ=DATABASE_ENVIRONMENT, repository_factory=factory) == 0
    assert "writes: none" in capsys.readouterr().out


def test_the_command_refuses_without_the_owner_or_database_and_prints_no_values(capsys):
    """@description: Missing settings refuse before any connection, naming only the missing setting names; failures print only a class name.
    @param capsys: Output capture.
    @return: None.
    """
    assert main(["status"], environ={}, repository_factory=lambda s, o: pytest.fail("connected")) == 1
    assert "OWNER_IDENTITY_MISSING_OR_INVALID" in capsys.readouterr().out
    broken = lambda settings, owner: (_ for _ in ()).throw(RuntimeError("secret-password-text"))
    assert main(["status"], environ=DATABASE_ENVIRONMENT, repository_factory=broken) == 1
    assert capsys.readouterr().out.strip() == "failed: RuntimeError"


SECRET_KEY_VALUE = "SYNTHETIC-KEY-MUST-NOT-APPEAR"  # [leak probe]: A refusal must never echo or even read this.


def combination(review: str, retention: str, **extra: str) -> dict:
    """@description: Build the environment mapping of one switch combination.
    @param review: JUKEBOX_AUTO_REVIEW value.
    @param retention: JUKEBOX_API_RETENTION value.
    @param extra: Further settings.
    @return: Mapping for load_auto_settings.
    """
    return {"JUKEBOX_AUTO_REVIEW": review, "JUKEBOX_API_RETENTION": retention, "YOUTUBE_API_KEY": SECRET_KEY_VALUE, **extra}


def test_automatic_review_without_retention_refuses_before_reading_any_credential():
    """@description: The refusal names the migration and the switch, says the system will not apply or change anything, and never touches the credential.
    @param: none
    @return: None.
    """
    with pytest.raises(ConfigurationError) as refusal:
        load_auto_settings(combination("on", "off"))
    text = str(refusal.value)
    assert "migration 0004" in text and "JUKEBOX_API_RETENTION" in text and "不會自動套用 migration" in text and "不會自行修改任何設定值" in text
    assert SECRET_KEY_VALUE not in text
    with pytest.raises(ConfigurationError, match="migration 0004"):
        load_auto_settings({"JUKEBOX_AUTO_REVIEW": "on"})


def test_the_other_switch_combinations_keep_their_behaviour():
    """@description: Both off keeps the manual flow; retention alone needs no credential; both on reads the key; settlement stays independent.
    @param: none
    @return: None.
    """
    both_off = load_auto_settings(combination("off", "off"))
    assert (both_off.review_enabled, both_off.api_retention, both_off.api_key) == (False, False, None)
    retention_only = load_auto_settings({"JUKEBOX_API_RETENTION": "on"})
    assert (retention_only.review_enabled, retention_only.api_retention, retention_only.api_key) == (False, True, None)
    both_on = load_auto_settings(combination("on", "on"))
    assert both_on.review_enabled and both_on.api_retention and both_on.api_key == SECRET_KEY_VALUE
    assert load_auto_settings({"JUKEBOX_AUTO_SETTLEMENT": "on"}).settlement_enabled is True


def test_retention_alone_purges_but_never_builds_or_calls_the_youtube_client(monkeypatch):
    """@description: With review off the pipeline has no provider: the purge runs and not one lookup is attempted.
    @param monkeypatch: Pytest patching.
    @return: None.
    """
    import bot.auto_startup as startup
    monkeypatch.setattr(startup, "YouTubeMetadataClient", lambda key: pytest.fail("the YouTube client must not be built"))
    repository = RecordingRepository()
    repository.pending_validation_operations = lambda limit, excluded=(): [("discord:1", "aaaaaaaaaaa")]
    repository.auto_review_subject = lambda operation: pytest.fail("no lookup may start")
    running = build_auto_pipeline(repository, load_auto_settings({"JUKEBOX_API_RETENTION": "on"}))
    running.sweep()
    assert running._metadata_client is None and "purge_api_observations" in repository.calls
    assert "revalidation_operations" not in repository.calls


def stub_startup(monkeypatch, environment: dict) -> list[str]:
    """@description: Replace everything main() reaches after configuration with a recorder, so only the order of refusals is observed.
    @param monkeypatch: Pytest patching.
    @param environment: Switch environment for this run.
    @return: Names of the stages main() reached.
    """
    import types
    import bot.__main__ as entry
    stages: list[str] = []
    for name in ("JUKEBOX_AUTO_REVIEW", "JUKEBOX_AUTO_SETTLEMENT", "JUKEBOX_API_RETENTION", "YOUTUBE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry, "load_production_discord_settings", lambda: types.SimpleNamespace(owner_id=1, application_id=2))
    monkeypatch.setattr(entry, "load_database_settings", lambda: "database-settings")
    monkeypatch.setattr(entry, "load_formal_exchange_enabled", lambda: False)
    monkeypatch.setattr(entry, "load_nickel_claim_enabled", lambda: False)
    monkeypatch.setattr(entry, "verify_mounted_secrets", lambda: None)
    monkeypatch.setattr(entry, "verify_retention_schema", lambda settings: stages.append("schema-check"))
    monkeypatch.setattr(entry, "SubmissionRepository", lambda *a, **k: (stages.append("repository"), (_ for _ in ()).throw(RuntimeError("stop")))[1])
    return stages


def test_main_refuses_review_without_retention_before_any_connection_or_gateway(monkeypatch):
    """@description: The refusal happens in configuration: no schema check, no repository, no Gateway.
    @param monkeypatch: Pytest patching.
    @return: None.
    """
    import bot.__main__ as entry
    stages = stub_startup(monkeypatch, {"JUKEBOX_AUTO_REVIEW": "on", "YOUTUBE_API_KEY": SECRET_KEY_VALUE})
    with pytest.raises(SystemExit) as refusal:
        entry.main()
    assert "migration 0004" in str(refusal.value) and SECRET_KEY_VALUE not in str(refusal.value) and stages == []


def test_main_checks_the_schema_once_when_retention_is_on_and_not_at_all_when_it_is_off(monkeypatch):
    """@description: Retention on verifies its tables and privileges before the repository exists; both off never looks at the retention schema.
    @param monkeypatch: Pytest patching.
    @return: None.
    """
    import bot.__main__ as entry
    for environment, expected in (({"JUKEBOX_API_RETENTION": "on"}, ["schema-check", "repository"]),
                                  ({"JUKEBOX_AUTO_REVIEW": "on", "JUKEBOX_API_RETENTION": "on", "YOUTUBE_API_KEY": SECRET_KEY_VALUE}, ["schema-check", "repository"]),
                                  ({}, ["repository"])):
        stages = stub_startup(monkeypatch, environment)
        with pytest.raises(RuntimeError, match="stop"):
            entry.main()
        assert stages == expected


def test_main_stops_when_the_retention_schema_check_refuses(monkeypatch):
    """@description: A refused schema check ends startup before the repository, the pipeline or the Gateway exist.
    @param monkeypatch: Pytest patching.
    @return: None.
    """
    import bot.__main__ as entry
    stages = stub_startup(monkeypatch, {"JUKEBOX_API_RETENTION": "on"})
    monkeypatch.setattr(entry, "verify_retention_schema", lambda settings: (_ for _ in ()).throw(ConfigurationError("table jb2_api_observations is missing")))
    with pytest.raises(SystemExit, match="jb2_api_observations"):
        entry.main()
    assert stages == []
