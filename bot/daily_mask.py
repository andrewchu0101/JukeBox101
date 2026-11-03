"""Half-mask a recommender's display name so a daily direct message never shows the full name.

Input is a live display name (never stored); output is one inert line that keeps the first and last visible character. Grapheme clusters are segmented by hand with the standard library because no dependency is approved.
"""

import unicodedata

ANONYMOUS_NAME = "匿名使用者"  # [Owner wording]: Shown when no usable name exists.
MASK_CHARACTER = "*"  # [Owner rule]: One mask character per hidden middle grapheme.
_ZERO_WIDTH_JOINER = "‍"  # [sequence glue]: Joins the next code point into the same visible character.
_LINE_BREAKING_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})  # [one-line rule]: Control characters and line or paragraph separators.
_EXTENDING_CATEGORIES = frozenset({"Mn", "Me", "Mc"})  # [attached marks]: Accents, variation selectors and keycap marks stay with their base.


def mask_display_name(name: str | None) -> str:
    """@description: Hide the middle of a display name so only a recognizable head and tail remain, and never a mention.
    @param name: Live display name, or None when Discord gave none.
    @return: Masked single-line name, a single mask for one character, or the anonymous placeholder for an empty name.
    """
    clusters = _graphemes(_single_line(name))  # [visible characters]: Whole grapheme clusters, never cut apart.
    if not clusters:
        return ANONYMOUS_NAME
    if len(clusters) == 1:
        return MASK_CHARACTER  # [never whole]: A one-character name would otherwise be shown completely.
    if len(clusters) == 2:
        return clusters[0] + MASK_CHARACTER
    return clusters[0] + MASK_CHARACTER * (len(clusters) - 2) + clusters[-1]


def _single_line(name: str | None) -> str:
    """@description: Turn line breaks and control characters into spaces and trim the ends so the result is always one line.
    @param name: Raw display name or None.
    @return: Trimmed text, empty when nothing visible remains.
    """
    if name is None:
        return ""
    spaced = "".join(" " if unicodedata.category(character) in _LINE_BREAKING_CATEGORIES else character for character in name)
    return spaced.strip()


def _graphemes(text: str) -> list[str]:
    """@description: Split text into user-perceived characters so emoji sequences and accented letters are never cut apart.
    @param text: Single-line text.
    @return: Grapheme clusters in order.
    """
    clusters: list[str] = []  # [segmented output]: Each entry is one visible character.
    joined_by_previous = False  # [ZWJ state]: True when the previous code point was a joiner, so this one attaches to it.
    for character in text:
        if clusters and (joined_by_previous or _extends_previous(character) or _completes_flag(clusters[-1], character)):
            clusters[-1] += character
        else:
            clusters.append(character)
        joined_by_previous = character == _ZERO_WIDTH_JOINER
    return clusters


def _extends_previous(character: str) -> bool:
    """@description: Tell whether a code point attaches to the character before it instead of standing alone.
    @param character: One code point.
    @return: True for combining marks, variation selectors, joiners, skin-tone modifiers and tag characters.
    """
    if character == _ZERO_WIDTH_JOINER or unicodedata.category(character) in _EXTENDING_CATEGORIES:
        return True
    return "\U0001F3FB" <= character <= "\U0001F3FF" or "\U000E0020" <= character <= "\U000E007F"


def _completes_flag(previous_cluster: str, character: str) -> bool:
    """@description: Pair two regional indicators into one flag.
    @param previous_cluster: Cluster built so far.
    @param character: Candidate second regional indicator.
    @return: True when the previous cluster is a single regional indicator and this one is too.
    """
    return _is_regional_indicator(character) and len(previous_cluster) == 1 and _is_regional_indicator(previous_cluster)


def _is_regional_indicator(character: str) -> bool:
    """@description: Recognize the code points that spell flags.
    @param character: One code point.
    @return: True for regional indicator letters.
    """
    return "\U0001F1E6" <= character <= "\U0001F1FF"
