"""Offline contract of the recommender half-mask shown in daily (and later exchange) direct messages.

INTERFACE CONTRACT (module bot/daily_mask.py, pure, stdlib only; no new dependency is approved, so grapheme clusters are segmented by hand
for: combining marks, variation selectors, skin-tone modifiers, ZWJ sequences, regional-indicator pairs, keycaps, and CRLF):
    ANONYMOUS_NAME: str = "匿名使用者"
    MASK_CHARACTER: str = "*"
    mask_display_name(name: str | None) -> str
        # Rules (Owner): keep the first and last grapheme and put one "*" per hidden middle grapheme between them;
        # 1 or 2 graphemes keep only the first grapheme (the second becomes "*"; a single grapheme becomes "*" so a one-character
        # name is never shown whole - QUESTION to lead, see the report); empty, None or whitespace-only gives ANONYMOUS_NAME.
        # Control characters and line/paragraph separators (newlines, tabs, U+2028, U+2029) are turned into a space before segmenting, so the result is always one line.
        # The result never equals the input for a name of two or more graphemes and never contains the hidden middle.
"""

import pytest

from bot.daily_mask import ANONYMOUS_NAME, MASK_CHARACTER, mask_display_name

FAMILY = "\U0001F468‍\U0001F469‍\U0001F467‍\U0001F466"  # [one grapheme]: Four people joined by zero-width joiners.
TAIWAN_FLAG = "\U0001F1F9\U0001F1FC"  # [one grapheme]: Two regional indicators.
THUMBS_UP_MEDIUM = "\U0001F44D\U0001F3FD"  # [one grapheme]: Base emoji plus skin-tone modifier.
GRINNING = "\U0001F600"  # [one grapheme]: A single emoji code point.
E_ACUTE_COMBINING = "é"  # [one grapheme]: Base letter plus combining accent.
HEART_VS16 = "❤️"  # [one grapheme]: Base symbol plus emoji variation selector.


@pytest.mark.parametrize("name, expected", [
    ("Alexander", "A*******r"),
    ("Bob", "B*b"),
    ("Abcd", "A**d"),
    ("Al", "A*"),
    ("小明", "小*"),
    ("王大明", "王*明"),
    ("王小大明", "王**明"),
    ("Ａｌｅｘ", "Ａ**ｘ"),
])
def test_names_keep_the_head_and_tail_and_hide_only_the_middle(name, expected) -> None:
    """@description: The Owner rule on plain text, CJK and full-width names, counted per character.
    @param name: Display name.
    @param expected: Masked text.
    @return: None.
    """
    assert mask_display_name(name) == expected


def test_a_one_character_name_is_never_shown_whole() -> None:
    """@description: A single grapheme would otherwise reveal the complete name, so it is fully masked.
    @return: None.
    """
    assert mask_display_name("A") == MASK_CHARACTER and mask_display_name(GRINNING) == MASK_CHARACTER


@pytest.mark.parametrize("name", ["", "   ", "\n\t ", None])
def test_an_empty_or_missing_name_shows_the_anonymous_placeholder(name) -> None:
    """@description: Nothing to mask means the fixed Chinese placeholder, and no exception.
    @param name: Empty-like name.
    @return: None.
    """
    assert mask_display_name(name) == ANONYMOUS_NAME == "匿名使用者"


@pytest.mark.parametrize("name, expected", [
    (f"{GRINNING}Smile{GRINNING}", f"{GRINNING}*****{GRINNING}"),
    (f"{FAMILY}abc", f"{FAMILY}**c"),
    (f"abc{FAMILY}", f"a**{FAMILY}"),
    (f"{TAIWAN_FLAG}x{TAIWAN_FLAG}", f"{TAIWAN_FLAG}*{TAIWAN_FLAG}"),
    (f"{THUMBS_UP_MEDIUM}Ok", f"{THUMBS_UP_MEDIUM}*k"),
    (f"{E_ACUTE_COMBINING}mile", f"{E_ACUTE_COMBINING}***e"),
    (f"{HEART_VS16}Love{HEART_VS16}", f"{HEART_VS16}****{HEART_VS16}"),
    (f"{FAMILY}{FAMILY}", f"{FAMILY}*"),
])
def test_an_emoji_or_accented_letter_is_one_unit_and_is_never_cut_apart(name, expected) -> None:
    """@description: Grapheme clusters stay whole: ZWJ families, flags, skin tones, combining accents and variation selectors.
    @param name: Display name with a multi-code-point cluster.
    @param expected: Masked text.
    @return: None.
    """
    result = mask_display_name(name)
    assert result == expected
    result.encode("utf-8")  # [valid text]: No lone surrogate or cut sequence.


@pytest.mark.parametrize("name", ["<@123456789012345678>", "<@!123456789012345678>", "<#123456789012345678>", "@everyone", "@here",
                                  "<@&123456789012345678>", "<:emoji:123456789012345678>"])
def test_mention_syntax_can_never_survive_the_mask(name) -> None:
    """@description: A hostile display name cannot turn into a clickable mention, a channel link, a role or a custom emoji.
    @param name: Mention-shaped display name.
    @return: None.
    """
    result = mask_display_name(name)
    assert name not in result and "<@" not in result and "<#" not in result and "@everyone" not in result and "@here" not in result
    assert result[0] == name[0] and result[-1] == name[-1] and set(result[1:-1]) == {MASK_CHARACTER}


@pytest.mark.parametrize("name", ["line1\nline2", "a\r\nb\r\nc", "tab\tname", "x y z"])
def test_a_multi_line_name_becomes_one_line(name) -> None:
    """@description: Newlines and other control characters cannot break the message layout or smuggle a second line.
    @param name: Name with line breaks.
    @return: None.
    """
    result = mask_display_name(name)
    assert all(character not in result for character in ("\n", "\r", "\t", " ", " "))


@pytest.mark.parametrize("name", ["Alexander", "Bob", "Al", "小明", "王大明", f"{GRINNING}Smile{GRINNING}", "ZzCanaryRecommenderName", "<@123456789012345678>"])
def test_the_result_is_deterministic_and_never_reveals_the_hidden_middle(name) -> None:
    """@description: Same input gives the same output, the full name is never present, and the middle letters are not shown.
    @param name: Display name of two or more characters.
    @return: None.
    """
    assert mask_display_name(name) == mask_display_name(name)
    assert name not in mask_display_name(name) and mask_display_name(name) != name


def test_hidden_middle_characters_do_not_appear_in_the_result() -> None:
    """@description: Check the secret part directly: every middle letter of a long name is absent from the masked text.
    @return: None.
    """
    result = mask_display_name("ZzCanaryRecommenderName")
    assert result == "Z" + "*" * 21 + "e" and "Canary" not in result and "Recommender" not in result
