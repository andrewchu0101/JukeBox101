"""Decide whether a new official video raises a doubt that it is another version of a song already in the catalog.

Inputs are official metadata read in memory only; outputs are concern codes and existing video IDs, never a title or channel.
A concern only sends a case to the Owner; this module never proves that two videos are, or are not, the same recording.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

from bot.youtube_metadata import VideoMetadata

DEFAULT_MAX_DURATION_GAP_SECONDS = 3  # [Owner rule]: Same channel and a duration this close (inclusive) is suspicious.
SIMILARITY_THRESHOLD = 0.65  # [conservative start]: Lower than common values so a false clearance costs less than a false concern; calibrate with real samples.
MIN_JUDGEABLE_TITLE_LENGTH = 3  # [comparison floor]: A shorter normalized title cannot be compared and must go to the Owner.
BRACKET_PAIRS = r"\([^()]*\)|（[^（）]*）|\[[^\[\]]*\]|【[^【】]*】|「[^「」]*」|『[^『』]*』|<[^<>]*>|《[^《》]*》"  # [label text]: Bracketed tags carry no song identity.
FEATURING_TAIL = re.compile(r"(?<![a-z0-9])(?:feat|ft|featuring)(?![a-z0-9]).*$")  # [credited artists]: Everything after the marker is dropped.
ENGLISH_KEYWORDS = ("remix\\w*", "cover", "live", "acoustic", "instrumental", "sped up", "slowed", "nightcore", "remaster\\w*",
                    "version", "mix", "edit", "demo", "unplugged", "karaoke", "8d", "lo-?fi", "bass boosted")  # [word-boundary terms]: Never matched inside another word.
ENGLISH_KEYWORD_PATTERN = re.compile(r"(?<![a-z0-9])(?:" + "|".join(ENGLISH_KEYWORDS) + r"|ver\.)(?![a-z])")
CHINESE_KEYWORDS = ("翻唱", "現場", "混音", "純音樂", "伴奏", "加速", "降速", "重製", "重新錄製")  # [substring terms]: Chinese has no word boundary.


class ConcernCode(Enum):
    """Name each reason a case must go to the Owner instead of being confirmed automatically."""

    SAME_CHANNEL_CLOSE_DURATION = "same_channel_close_duration"
    SIMILAR_TITLE = "similar_title"
    VERSION_KEYWORD = "version_keyword"
    TITLE_UNJUDGEABLE = "title_unjudgeable"
    COMPARISON_INCOMPLETE = "comparison_incomplete"


@dataclass(frozen=True)
class VersionConcern:
    """Carry one doubt as a code and the existing video it concerns; the type has no room for a title."""

    code: ConcernCode  # [reason]: Why the Owner must look.
    other_video_id: str | None  # [existing video]: Linked on the Owner card; None for doubts about the candidate alone.


@dataclass(frozen=True)
class ConcernThresholds:
    """Hold the tunable limits as named values so a policy change is visible in one place."""

    max_duration_gap_seconds: int = DEFAULT_MAX_DURATION_GAP_SECONDS  # [duration rule]: Inclusive.
    min_title_similarity: float = SIMILARITY_THRESHOLD  # [title rule]: Inclusive minimum difflib ratio.
    min_judgeable_title_length: int = MIN_JUDGEABLE_TITLE_LENGTH  # [comparison floor]: Shorter normalized titles are unjudgeable.


def normalize_title(title: str | None) -> str:
    """@description: Reduce an upload title to comparable text so decorations do not hide a repeated song.
    @param title: Official title, or None.
    @return: Lowercase text without brackets, credited artists, punctuation or spaces; empty for a missing title.
    """
    text = unicodedata.normalize("NFKC", title or "").casefold()
    previous = None
    while previous != text:
        previous, text = text, re.sub(BRACKET_PAIRS, " ", text)
    text = FEATURING_TAIL.sub("", text)
    return "".join(character for character in text if character.isalnum())


def title_similarity(first: str, second: str) -> float:
    """@description: Measure how alike two titles are after normalization, as a hint only.
    @param first: First title.
    @param second: Second title.
    @return: difflib ratio between 0.0 and 1.0.
    """
    return SequenceMatcher(None, normalize_title(first), normalize_title(second)).ratio()


def has_version_keyword(title: str | None) -> bool:
    """@description: Detect words that name a different version, since such a video needs the Owner's look.
    @param title: Official title, or None.
    @return: True when a version keyword is present as a whole word (English) or substring (Chinese).
    """
    text = unicodedata.normalize("NFKC", title or "").casefold()
    return ENGLISH_KEYWORD_PATTERN.search(text) is not None or any(keyword in text for keyword in CHINESE_KEYWORDS)


def _is_similar(first_title: str | None, second_title: str | None, thresholds: ConcernThresholds) -> bool:
    """@description: Apply the ratio rule and the containment rule, because short titles make the ratio swing.
    @param first_title: Candidate title.
    @param second_title: Existing title.
    @param thresholds: Active limits.
    @return: True when the titles are alike enough to ask the Owner.
    """
    first, second = normalize_title(first_title), normalize_title(second_title)
    shorter, longer = sorted((first, second), key=len)
    if len(shorter) < thresholds.min_judgeable_title_length:
        return False
    return shorter in longer or title_similarity(first, second) >= thresholds.min_title_similarity


def _is_close_duration(candidate: VideoMetadata, other: VideoMetadata, thresholds: ConcernThresholds) -> bool:
    """@description: Apply the same-channel, near-equal-duration rule.
    @param candidate: New video.
    @param other: Existing video.
    @param thresholds: Active limits.
    @return: True when both videos share a channel and their durations differ by no more than the limit.
    """
    if candidate.channel_id is None or candidate.channel_id != other.channel_id:
        return False
    if candidate.duration_seconds is None or other.duration_seconds is None:
        return False
    return abs(candidate.duration_seconds - other.duration_seconds) <= thresholds.max_duration_gap_seconds


def concerns_against(candidate: VideoMetadata, other: VideoMetadata, thresholds: ConcernThresholds) -> tuple[VersionConcern, ...]:
    """@description: Compare the new video with one existing video, reporting each pair signal independently.
    @param candidate: New video.
    @param other: Existing video from the recent catalog.
    @param thresholds: Active limits.
    @return: Concerns naming the existing video; empty when neither signal applies.
    """
    found = []  # [pair signals]: Both may apply to the same pair.
    if _is_close_duration(candidate, other, thresholds):
        found.append(VersionConcern(ConcernCode.SAME_CHANNEL_CLOSE_DURATION, other.video_id))
    if _is_similar(candidate.title, other.title, thresholds):
        found.append(VersionConcern(ConcernCode.SIMILAR_TITLE, other.video_id))
    return tuple(found)


def assess_concerns(candidate: VideoMetadata, others: Iterable[VideoMetadata],
                    thresholds: ConcernThresholds = ConcernThresholds()) -> tuple[VersionConcern, ...]:
    """@description: Collect every doubt about the new video, so only a clean result may be confirmed without the Owner.
    @param candidate: New video.
    @param others: Existing videos from the recent catalog, compared in memory only.
    @param thresholds: Active limits.
    @return: Concerns in a stable order; empty means no doubt, which is not proof of a new version.
    """
    found = []  # [doubts]: Candidate-only doubts first, then one entry per matching pair signal.
    if len(normalize_title(candidate.title)) < thresholds.min_judgeable_title_length:
        found.append(VersionConcern(ConcernCode.TITLE_UNJUDGEABLE, None))
    if has_version_keyword(candidate.title):
        found.append(VersionConcern(ConcernCode.VERSION_KEYWORD, None))
    for other in others:
        found.extend(concerns_against(candidate, other, thresholds))
    return tuple(found)
