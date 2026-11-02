"""Owner helper: turn one YouTube video link into the allowlist line for bot.env, at the cost of one API request.

Input is a single video URL and the API key from the environment; output is exactly one printed line, `YOUTUBE_TRUSTED_CHANNEL_IDS=<UC...>`.
It prints no title, channel name or key, writes no file or database row, edits no setting, and needs no automatic mode or Discord setting.
"""

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from typing import Any

from bot.config import TRUSTED_CHANNELS_ENV, ConfigurationError, load_trusted_channel_ids, load_youtube_api_key
from bot.urls import InvalidVideoUrl, parse_youtube_url
from bot.youtube_metadata import LookupStatus, YouTubeMetadataClient

FAILURE_CODES = {LookupStatus.QUOTA_EXHAUSTED: "API_UNAVAILABLE", LookupStatus.TEMPORARY_FAILURE: "API_UNAVAILABLE",
                 LookupStatus.NOT_FOUND: "VIDEO_NOT_FOUND"}  # [stable codes]: Quota and outage share one code; a missing video is a refusal.
REFUSAL_CODES = frozenset({"VIDEO_NOT_FOUND"})  # [refusal, not failure]: The input cannot be used, retrying will not help.


class ToolStopped(Exception):
    """Carry a stable outcome code and whether it is a refusal or a failure; never a message that could echo input."""

    def __init__(self, code: str, refused: bool) -> None:
        """@description: Keep only the code and its kind.
        @param self: New stop.
        @param code: Stable outcome code.
        @param refused: True prints `refused:`, False prints `failed:`.
        @return: None.
        """
        super().__init__(code)
        self.code = code  # [printed]: The only text the Owner sees.
        self.refused = refused  # [prefix choice]: Refusal or failure.


def _parser() -> argparse.ArgumentParser:
    """@description: Define the single positional argument; anything else exits through argparse before any request.
    @param: none
    @return: Argument parser.
    """
    parser = argparse.ArgumentParser(prog="channel_id_tool", description="Print the allowlist line for one YouTube video's channel.")
    parser.add_argument("video_url", help="One YouTube video link.")
    return parser


def _channel_line(video_id: str, environ: Mapping[str, str], client_factory: Callable[[str], Any]) -> str:
    """@description: Ask the official API about one video and build the allowlist line from its channel ID.
    @param video_id: Parser-validated video.
    @param environ: Environment holding the API key.
    @param client_factory: Builds the metadata client from the key.
    @return: The line to paste into bot.env.
    """
    try:
        api_key = load_youtube_api_key(environ)
    except ConfigurationError:
        raise ToolStopped("CONFIGURATION_ERROR", True) from None
    lookup = client_factory(api_key).lookup(video_id)
    if lookup.status in FAILURE_CODES:
        code = FAILURE_CODES[lookup.status]
        raise ToolStopped(code, code in REFUSAL_CODES)
    return f"{TRUSTED_CHANNELS_ENV}={_valid_channel_id(lookup.metadata.channel_id)}"


def _valid_channel_id(channel_id: object) -> str:
    """@description: Accept only a value the Bot itself would accept at startup, so a pasted line can never stop the Bot.
    @param channel_id: Channel ID from the response.
    @return: The same channel ID.
    """
    if not isinstance(channel_id, str) or not channel_id:
        raise ToolStopped("CHANNEL_ID_INVALID", True)
    try:
        load_trusted_channel_ids({TRUSTED_CHANNELS_ENV: channel_id})
    except ConfigurationError:
        raise ToolStopped("CHANNEL_ID_INVALID", True) from None
    return channel_id


def main(argv: list | None = None, *, environ: Mapping[str, str] | None = None,
         client_factory: Callable[[str], Any] = YouTubeMetadataClient) -> int:
    """@description: Print the allowlist line for one video link, or one stable failure code on stderr.
    @param argv: Arguments after the program name, or None for the process arguments.
    @param environ: Environment mapping, or None for the process environment.
    @param client_factory: Builds the metadata client from the key; tests inject a scripted one.
    @return: Process exit code: 0 on success, 1 on any refusal or failure.
    """
    arguments = _parser().parse_args(argv)
    try:
        video = parse_youtube_url(arguments.video_url)
        print(_channel_line(video.video_id, os.environ if environ is None else environ, client_factory))
    except InvalidVideoUrl:
        print("refused: INVALID_VIDEO_URL", file=sys.stderr)
        return 1
    except ToolStopped as stop:
        print(f"{'refused' if stop.refused else 'failed'}: {stop.code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
