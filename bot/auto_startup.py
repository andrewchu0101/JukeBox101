"""Read the automatic review and settlement modes and build their pipeline at the process boundary.

Inputs are environment settings only; both modes are off by default, and a switched-on mode with invalid settings refuses to start.
"""

from dataclasses import dataclass
from typing import Any

from bot.auto_pipeline import AutoPipeline
from bot.config import load_api_retention_enabled, load_auto_review_enabled, load_auto_settlement_enabled, load_trusted_channel_ids, load_youtube_api_key
from bot.youtube_metadata import YouTubeMetadataClient


@dataclass(frozen=True)
class AutoSettings:
    """Carry the validated automatic modes; the representation hides the credential."""

    review_enabled: bool  # [lookups]: Official API calls may run.
    settlement_enabled: bool  # [money]: Committed qualifications may pay.
    trusted_channel_ids: frozenset[str]  # [allowlist]: Empty approves nothing automatically.
    api_key: str | None  # [credential]: Present only when lookups may run.
    api_retention: bool = False  # [API data lifecycle]: Official values live only in the short-lived table, and the purge job runs.

    def __repr__(self) -> str:
        """@description: Keep the credential out of every representation.
        @param self: Settings.
        @return: Text without the key.
        """
        return f"AutoSettings(review_enabled={self.review_enabled}, settlement_enabled={self.settlement_enabled}, api_retention={self.api_retention})"


def load_auto_settings(environ=None) -> AutoSettings:
    """@description: Validate the automatic modes together, reading a credential only when it is needed.
    @param environ: Mapping to read; None reads the process environment.
    @return: Validated settings.
    """
    review_enabled = load_auto_review_enabled(environ)
    return AutoSettings(review_enabled, load_auto_settlement_enabled(environ), load_trusted_channel_ids(environ),
                        load_youtube_api_key(environ) if review_enabled else None, load_api_retention_enabled(environ))


def build_auto_pipeline(repository: Any, settings: AutoSettings) -> AutoPipeline | None:
    """@description: Build the single pipeline for this process, or none when every automatic mode is off.
    @param repository: Submission repository shared with the rest of the entry.
    @param settings: Validated automatic modes.
    @return: Pipeline, or None so existing replies stay unchanged.
    """
    if not settings.review_enabled and not settings.settlement_enabled and not settings.api_retention:
        return None
    client = YouTubeMetadataClient(settings.api_key) if settings.review_enabled else None
    return AutoPipeline(repository, client, settings.trusted_channel_ids, settlement_enabled=settings.settlement_enabled,
                        api_retention=settings.api_retention)
