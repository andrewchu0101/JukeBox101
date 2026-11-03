"""Verify the read-only `review-reasons` operator subcommand offline.

It asks the repository once as the configured Owner, prints reason counts and the repository's pending cases as codes and canonical links,
never a title or channel, accepts no actor argument, and never calls a writing method. The repository is a recording fake; no database is opened.
"""

import pytest

from bot.owner_cli import main

OWNER = 777
ENVIRONMENT = {"DISCORD_OWNER_USER_ID": str(OWNER), "DB_HOST": "127.0.0.1", "DB_PORT": "5432", "DB_NAME": "jukebox101_test",
               "DB_USER": "jukebox101_app", "DB_PASSWORD": "synthetic-not-a-secret"}  # [synthetic environment]: Only for the offline factory.
RESULT = {"reason_counts": {"music_category": 12, "title_needs_owner": 3, "other": 1},
          "pending": [{"accepted_sequence": 5, "operation_id": "op-five", "video_id": "abcdefghijk", "reason": "title_needs_owner", "concerns": []},
                      {"accepted_sequence": 9, "operation_id": "op-nine", "video_id": "lmnopqrstuv", "reason": "music_category_version_concern",
                       "concerns": ["similar_title", "version_keyword"]}]}
FORBIDDEN = ("title", "channel", "duration", "submitter")  # [leak markers]: Never printed as data labels; code names are removed before checking.


class ReadOnlyRepository:
    """Answer only owner_review_reasons and record how it was called; any other attribute is a write attempt."""

    def __init__(self, result: dict, failure: Exception | None = None) -> None:
        """@description: Store the canned result or the failure to raise.
        @param self: Fake repository.
        @param result: Result to return.
        @param failure: Exception to raise instead, or None.
        @return: None.
        """
        self.result, self.failure, self.calls = result, failure, []

    def owner_review_reasons(self, requester_id: int, days: int) -> dict:
        """@description: Record the call and answer.
        @param self: Fake repository.
        @param requester_id: Identity asking.
        @param days: Look-back window.
        @return: Canned result.
        """
        self.calls.append((requester_id, days))
        if self.failure is not None:
            raise self.failure
        return self.result

    def __getattr__(self, name: str):
        """@description: Fail loudly on any other repository method, because the command must be read-only.
        @param self: Fake repository.
        @param name: Attribute asked for.
        @return: Never returns.
        """
        raise AssertionError(f"review-reasons must not touch repository.{name}")


def run(repository, capsys, argv=("review-reasons",)) -> tuple[int, str]:
    """@description: Run the CLI with the fake repository.
    @param repository: Fake repository.
    @param capsys: Output capture.
    @param argv: Arguments after the program name.
    @return: Exit code and printed text.
    """
    code = main(list(argv), environ=ENVIRONMENT, repository_factory=lambda settings, owner: repository)
    return code, capsys.readouterr().out


def test_it_asks_once_as_the_owner_with_seven_days_by_default_and_prints_counts_and_cases(capsys) -> None:
    """@description: Defaults, identity and the printed codes, links and ordering fields.
    @param capsys: Output capture.
    @return: None.
    """
    repository = ReadOnlyRepository(RESULT)
    code, output = run(repository, capsys)
    assert code == 0 and repository.calls == [(OWNER, 7)]
    for expected in ("music_category", "12", "title_needs_owner", "other", "op-five", "op-nine", "https://www.youtube.com/watch?v=abcdefghijk",
                     "https://www.youtube.com/watch?v=lmnopqrstuv", "music_category_version_concern", "similar_title", "version_keyword"):
        assert expected in output
    assert output.index("op-five") < output.index("op-nine")


def test_the_days_option_is_passed_through(capsys) -> None:
    """@description: --days selects the window.
    @param capsys: Output capture.
    @return: None.
    """
    repository = ReadOnlyRepository(RESULT)
    code, _output = run(repository, capsys, ("review-reasons", "--days", "30"))
    assert code == 0 and repository.calls == [(OWNER, 30)]


@pytest.mark.parametrize("days", ["0", "-1", "61", "abc"])
def test_a_days_value_outside_one_to_sixty_is_refused_before_reading(days, capsys) -> None:
    """@description: The window is capped at sixty days and never reaches the repository when invalid.
    @param days: Invalid option text.
    @param capsys: Output capture.
    @return: None.
    """
    repository = ReadOnlyRepository(RESULT)
    try:
        code, _output = run(repository, capsys, ("review-reasons", "--days", days))
        assert code == 1
    except SystemExit:
        pass
    assert repository.calls == []


def test_sixty_days_is_accepted(capsys) -> None:
    """@description: The upper bound is inclusive.
    @param capsys: Output capture.
    @return: None.
    """
    repository = ReadOnlyRepository(RESULT)
    assert run(repository, capsys, ("review-reasons", "--days", "60"))[0] == 0 and repository.calls == [(OWNER, 60)]


def test_the_output_has_no_title_channel_duration_or_submitter_labels(capsys) -> None:
    """@description: Only counts, codes, operation ids and canonical links are printed.
    @param capsys: Output capture.
    @return: None.
    """
    _code, output = run(ReadOnlyRepository(RESULT), capsys)
    lowered = output.lower().replace("title_needs_owner", "").replace("similar_title", "")
    assert not any(word in lowered for word in FORBIDDEN)


def test_an_empty_result_is_a_normal_answer(capsys) -> None:
    """@description: No decisions and no pending cases exit 0 without error text.
    @param capsys: Output capture.
    @return: None.
    """
    code, output = run(ReadOnlyRepository({"reason_counts": {}, "pending": []}), capsys)
    assert code == 0 and output.strip() and "failed" not in output and "refused" not in output


def test_a_non_owner_refusal_exits_nonzero_without_printing_rows(capsys) -> None:
    """@description: PermissionError from the repository is reported by class name only.
    @param capsys: Output capture.
    @return: None.
    """
    code, output = run(ReadOnlyRepository(RESULT, PermissionError("OWNER_ONLY")), capsys)
    assert code == 1 and "op-five" not in output and "failed" in output


def test_an_actor_argument_is_not_accepted(capsys) -> None:
    """@description: The Owner identity comes only from configuration.
    @param capsys: Output capture.
    @return: None.
    """
    repository = ReadOnlyRepository(RESULT)
    with pytest.raises(SystemExit):
        run(repository, capsys, ("review-reasons", "--requester", "999"))
    assert repository.calls == []
