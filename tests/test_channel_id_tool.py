"""Verify the Owner's channel-ID helper: paste a video link, get a line that can be pasted into bot.env, at a cost of one API request.

Spec (module bot.channel_id_tool, run as `python -m bot.channel_id_tool <video-url>`):
- accepts exactly one YouTube video URL (the shared parser decides); anything else is refused before any API call;
- reads the API key from YOUTUBE_API_KEY or YOUTUBE_API_KEY_FILE (it does not need any automatic mode switched on, and needs no database or Discord setting);
- makes one lookup through the shared metadata client (one quota unit) and prints exactly `YOUTUBE_TRUSTED_CHANNEL_IDS=<UC...>`;
- never prints a title, a channel name or the key, never writes a file or a database row, and never edits the allowlist itself;
- refusals print `refused: <CODE>` and API trouble prints `failed: <CODE>`, both with exit code 1.
The provider is a scripted transport, so no test reaches the network.
"""

import os

import pytest

from bot.channel_id_tool import main
from bot.youtube_metadata import TransportFailure, YouTubeMetadataClient
from tests.test_youtube_metadata import TRUSTED, VIDEO_ID, ScriptedTransport, api_item

URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
KEY_CANARY = "synthetic-key-canary-5521"
TITLE_CANARY = "TitleCanaryZq9"
ENVIRONMENT = {"YOUTUBE_API_KEY": KEY_CANARY}


def run(argv, transport, environment=None) -> int:
    """@description: Run the tool with a scripted transport and a synthetic environment.
    @param argv: Arguments after the program name.
    @param transport: Scripted transport.
    @param environment: Environment mapping; None uses the key-only default.
    @return: Exit code.
    """
    factory = lambda key: YouTubeMetadataClient(key, transport=transport, sleep=lambda _seconds: None)  # noqa: E731
    return main(argv, environ=ENVIRONMENT if environment is None else environment, client_factory=factory)


def test_a_video_link_prints_one_pasteable_line_and_costs_one_request(capsys) -> None:
    """@description: The happy path prints the bot.env line and spends exactly one quota unit.
    @param capsys: Output capture.
    @return: None.
    """
    transport = ScriptedTransport({"items": [api_item()]})
    assert run([URL], transport) == 0
    assert capsys.readouterr().out == f"YOUTUBE_TRUSTED_CHANNEL_IDS={TRUSTED}\n"
    assert [call[0] for call in transport.calls] == [VIDEO_ID]


@pytest.mark.parametrize("url", [f"https://youtu.be/{VIDEO_ID}", f"https://music.youtube.com/watch?v={VIDEO_ID}&utm_source=x",
                                 f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PLabc"])
def test_every_supported_link_shape_resolves_to_the_same_video(url, capsys) -> None:
    """@description: The shared parser normalises tracking parameters and short links.
    @param url: Equivalent link shape.
    @param capsys: Output capture.
    @return: None.
    """
    transport = ScriptedTransport({"items": [api_item()]})
    assert run([url], transport) == 0 and transport.calls[0][0] == VIDEO_ID
    assert capsys.readouterr().out.strip() == f"YOUTUBE_TRUSTED_CHANNEL_IDS={TRUSTED}"


def test_output_never_contains_a_title_a_channel_name_or_the_key(capsys) -> None:
    """@description: Only the channel ID is printed, on either stream.
    @param capsys: Output capture.
    @return: None.
    """
    item = api_item(snippet={"channelId": TRUSTED, "categoryId": "10", "title": TITLE_CANARY, "channelTitle": "ChannelNameCanary",
                             "liveBroadcastContent": "none"})
    run([URL], ScriptedTransport({"items": [item]}))
    captured = capsys.readouterr()
    assert all(canary not in captured.out + captured.err for canary in (TITLE_CANARY, "ChannelNameCanary", KEY_CANARY))


@pytest.mark.parametrize("url", ["not a url", "https://example.com/watch?v=abc", "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
                                 "https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa", "https://www.youtube.com/@somehandle", ""])
def test_a_link_that_is_not_a_video_is_refused_without_any_request(url, capsys) -> None:
    """@description: Spotify tracks, channel pages, handles and garbage never reach the provider.
    @param url: Unsupported input.
    @param capsys: Output capture.
    @return: None.
    """
    transport = ScriptedTransport()
    assert run([url], transport) == 1 and transport.calls == []
    captured = capsys.readouterr()
    assert captured.err.startswith("refused: ") and captured.out == ""


def test_missing_arguments_make_no_request() -> None:
    """@description: Running with no link or several links does nothing.
    @param: none
    @return: None.
    """
    transport = ScriptedTransport()
    for argv in ([], [URL, URL]):
        with pytest.raises(SystemExit):
            run(argv, transport)
    assert transport.calls == []


def test_a_missing_key_is_refused_without_a_request_and_without_echoing_anything(capsys) -> None:
    """@description: The refusal names a code, not a path or a value.
    @param capsys: Output capture.
    @return: None.
    """
    transport = ScriptedTransport()
    assert run([URL], transport, environment={}) == 1 and transport.calls == []
    assert "refused: CONFIGURATION_ERROR" in capsys.readouterr().err


def test_the_tool_works_with_every_automatic_mode_off_and_needs_no_database_setting(capsys) -> None:
    """@description: The Owner can fetch an ID before turning anything on; only the key is required.
    @param capsys: Output capture.
    @return: None.
    """
    assert run([URL], ScriptedTransport({"items": [api_item()]}), environment={"YOUTUBE_API_KEY": KEY_CANARY, "JUKEBOX_AUTO_REVIEW": "off"}) == 0
    assert capsys.readouterr().out.startswith("YOUTUBE_TRUSTED_CHANNEL_IDS=UC")


def test_a_video_that_does_not_exist_is_refused(capsys) -> None:
    """@description: An empty result names no channel.
    @param capsys: Output capture.
    @return: None.
    """
    assert run([URL], ScriptedTransport({"items": []})) == 1
    assert "refused: VIDEO_NOT_FOUND" in capsys.readouterr().err


@pytest.mark.parametrize("failure", [TransportFailure(403, "quotaExceeded"), TransportFailure(503, ""), TransportFailure(None, "")])
def test_api_trouble_is_reported_as_unavailable_with_exit_one_and_prints_no_id(failure, capsys) -> None:
    """@description: Quota exhaustion and outages print a failure code, never a partial result.
    @param failure: Provider failure on every attempt.
    @param capsys: Output capture.
    @return: None.
    """
    assert run([URL], ScriptedTransport(failure, failure, failure)) == 1
    captured = capsys.readouterr()
    assert "failed: API_UNAVAILABLE" in captured.err and "YOUTUBE_TRUSTED_CHANNEL_IDS" not in captured.out


@pytest.mark.parametrize("channel", [None, "", "@handle", "UCshort", "ucaaaaaaaaaaaaaaaaaaaaaa"])
def test_a_response_without_a_valid_channel_id_is_refused(channel, capsys) -> None:
    """@description: The printed value must pass the same check the Bot applies at startup.
    @param channel: Malformed or absent channel ID in the response.
    @param capsys: Output capture.
    @return: None.
    """
    item = api_item(snippet={"channelId": channel, "categoryId": "10", "title": "Song", "liveBroadcastContent": "none"})
    assert run([URL], ScriptedTransport({"items": [item]})) == 1
    assert "refused: CHANNEL_ID_INVALID" in capsys.readouterr().err


def test_the_tool_writes_no_file_and_changes_no_environment(tmp_path, monkeypatch) -> None:
    """@description: It only prints; the Owner pastes the line into bot.env.
    @param tmp_path: Empty working directory.
    @param monkeypatch: Working-directory switch.
    @return: None.
    """
    monkeypatch.chdir(tmp_path)
    before = dict(os.environ)
    run([URL], ScriptedTransport({"items": [api_item()]}))
    assert list(tmp_path.iterdir()) == [] and dict(os.environ) == before


def test_the_tool_never_opens_a_database_connection(monkeypatch) -> None:
    """@description: Channel lookup is independent of the database.
    @param monkeypatch: Connection trap.
    @return: None.
    """
    def forbidden(*_arguments, **_keywords):
        raise AssertionError("channel tool opened a connection")
    monkeypatch.setattr("psycopg.connect", forbidden)
    assert run([URL], ScriptedTransport({"items": [api_item()]})) == 0
