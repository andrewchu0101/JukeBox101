"""Verify the sanitised .env.example documents the trust mode with the closed default and never ships the open mode switched on."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def active_lines() -> dict:
    """@description: Read the uncommented NAME=value lines of .env.example.
    @param: none
    @return: Mapping of setting names to values.
    """
    pairs = (line.split("=", 1) for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
             if "=" in line and not line.lstrip().startswith("#"))
    return {name.strip(): value.strip() for name, value in pairs}


def test_the_trust_mode_is_listed_with_the_allowlist_default() -> None:
    """@description: A new operator copying the file gets today's behaviour.
    @param: none
    @return: None.
    """
    assert active_lines().get("JUKEBOX_AUTO_TRUST_MODE") == "allowlist"


def test_the_example_never_switches_the_open_mode_or_its_prerequisites_on() -> None:
    """@description: Every automatic switch in the example stays off.
    @param: none
    @return: None.
    """
    values = active_lines()
    assert "music_category" not in values.values()
    assert all(values.get(name, "off") == "off" for name in ("JUKEBOX_AUTO_REVIEW", "JUKEBOX_AUTO_SETTLEMENT", "JUKEBOX_API_RETENTION",
                                                              "JUKEBOX_AUTO_VERSION_CONFIRM"))


def test_the_comment_next_to_the_mode_names_its_prerequisites() -> None:
    """@description: The file explains that music_category needs review, retention and the version comparison.
    @param: none
    @return: None.
    """
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    start = text.index("JUKEBOX_AUTO_TRUST_MODE")
    window = text[max(0, start - 600):start + 200]
    assert all(name in window for name in ("JUKEBOX_AUTO_REVIEW", "JUKEBOX_API_RETENTION", "JUKEBOX_AUTO_VERSION_CONFIRM", "music_category"))
