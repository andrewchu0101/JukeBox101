"""Verify the `conflict-resolve` operator subcommand offline: a preview writes nothing, a write needs the digest of the previewed inputs, it refuses a video
that is not in conflict and a kept record that is not an Owner version record, and it asks the repository once as the configured Owner.

The repository is a recording fake; no database is opened.
"""

from types import SimpleNamespace

import pytest

from bot.owner_cli import conflict_digest, main
from bot.version_identity import VersionIdentityStatus

OWNER = 777
ENVIRONMENT = {"DISCORD_OWNER_USER_ID": str(OWNER), "DB_HOST": "127.0.0.1", "DB_PORT": "5432", "DB_NAME": "jukebox101_test",
               "DB_USER": "jukebox101_app", "DB_PASSWORD": "synthetic-not-a-secret"}  # [synthetic environment]: Only for the offline factory.
REASON = "以 Owner 手動確認的版本為準"
OWNER_RECORD = SimpleNamespace(evidence_id="owner-1", source_type=SimpleNamespace(value="owner_review"), version=SimpleNamespace(value="rv-owner"))
SYSTEM_RECORD = SimpleNamespace(evidence_id="system-1", source_type=SimpleNamespace(value="system_judgement"), version=SimpleNamespace(value="rv-system"))


class FakeRepository:
    """Answer the Owner view and record every resolution call."""

    def __init__(self, status: VersionIdentityStatus = VersionIdentityStatus.CONFLICT) -> None:
        """@description: Store the canned view.
        @param self: Fake repository.
        @param status: Identity status the view reports.
        @return: None.
        """
        self.view = SimpleNamespace(identity=SimpleNamespace(status=status), review=SimpleNamespace(version=2), evidence=(OWNER_RECORD, SYSTEM_RECORD))
        self.resolutions = []

    def review_view(self, actor_id: int, operation_id: str):
        """@description: Return the canned view.
        @param self: Fake repository.
        @param actor_id: Caller.
        @param operation_id: Operation.
        @return: View.
        """
        return self.view

    def resolve_conflict(self, requester_id, decision_id, operation_id, keep_evidence_id, reason, expected_revision):
        """@description: Record the call.
        @param self: Fake repository.
        @param requester_id: Caller.
        @param decision_id: Decision key.
        @param operation_id: Operation.
        @param keep_evidence_id: Kept record.
        @param reason: Explanation.
        @param expected_revision: Revision.
        @return: Result with a review status and revision.
        """
        self.resolutions.append((requester_id, decision_id, operation_id, keep_evidence_id, reason, expected_revision))
        return SimpleNamespace(status=SimpleNamespace(value="approved"), version=expected_revision + 1)


def run(repository, argv, capsys) -> tuple[int, str]:
    """@description: Run the CLI with the fake repository.
    @param repository: Fake repository.
    @param argv: Arguments after the program name.
    @param capsys: Output capture.
    @return: Exit code and output.
    """
    code = main(list(argv), environ=ENVIRONMENT, repository_factory=lambda settings, owner: repository)
    return code, capsys.readouterr().out


def base(action: str, keep: str = "owner-1") -> list[str]:
    """@description: Build the common arguments.
    @param action: preview or write.
    @param keep: Evidence to keep.
    @return: Argument list.
    """
    return ["conflict-resolve", action, "--operation", "discord:9", "--keep-evidence", keep, "--reason", REASON]


def test_a_preview_prints_the_digest_and_writes_nothing(capsys) -> None:
    """@description: The preview lists the records and the digest of exactly the previewed inputs.
    @param capsys: Output capture.
    @return: None.
    """
    repository = FakeRepository()
    code, output = run(repository, base("preview"), capsys)
    assert code == 0 and repository.resolutions == []
    assert conflict_digest("discord:9", 2, "owner-1", REASON, OWNER) in output and "rv-owner" in output


def test_a_write_with_the_right_digest_resolves_once_as_the_owner(capsys) -> None:
    """@description: The write passes the Owner, the kept record and the examined revision to the repository exactly once.
    @param capsys: Output capture.
    @return: None.
    """
    repository = FakeRepository()
    digest = conflict_digest("discord:9", 2, "owner-1", REASON, OWNER)
    code, output = run(repository, [*base("write"), "--expected-revision", "2", "--digest", digest], capsys)
    assert code == 0 and "resolved" in output and len(repository.resolutions) == 1
    assert repository.resolutions[0][0] == OWNER and repository.resolutions[0][2:] == ("discord:9", "owner-1", REASON, 2)


@pytest.mark.parametrize("extra", [["--expected-revision", "2", "--digest", "0" * 64], ["--expected-revision", "3", "--digest", "PLACEHOLDER"]])
def test_a_wrong_digest_or_revision_is_refused_without_writing(capsys, extra) -> None:
    """@description: The write must match the previewed inputs.
    @param capsys: Output capture.
    @param extra: Wrong revision or digest arguments.
    @return: None.
    """
    repository = FakeRepository()
    if extra[-1] == "PLACEHOLDER":
        extra[-1] = conflict_digest("discord:9", 2, "owner-1", REASON, OWNER)
    code, output = run(repository, [*base("write"), *extra], capsys)
    assert code == 1 and "CONFIRMATION_DIGEST_MISMATCH" in output and repository.resolutions == []


def test_a_video_that_is_not_in_conflict_is_refused(capsys) -> None:
    """@description: Only a frozen conflict can be resolved.
    @param capsys: Output capture.
    @return: None.
    """
    repository = FakeRepository(VersionIdentityStatus.CONFIRMED)
    code, output = run(repository, base("preview"), capsys)
    assert code == 1 and "NOT_IN_CONFLICT" in output


def test_a_system_record_or_unknown_record_cannot_be_kept(capsys) -> None:
    """@description: The record to keep must be an Owner version record of this video.
    @param capsys: Output capture.
    @return: None.
    """
    for keep in ("system-1", "missing"):
        code, output = run(FakeRepository(), base("preview", keep), capsys)
        assert code == 1 and "KEPT_RECORD_NOT_AN_OWNER_VERSION_RECORD" in output
