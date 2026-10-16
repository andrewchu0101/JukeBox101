"""Check full-video duration boundaries using synthetic evidence only.

Exact numeric values and explicit provenance distinguish rejection from missing validation.
"""

from dataclasses import replace
from decimal import Decimal

import pytest

from bot.eligibility import DurationEligibility, DurationEvidence, assess_duration
from bot.song_identity import VideoIdentity

VIDEO = VideoIdentity("Abc_123-Xyz")


@pytest.fixture
def duration_evidence():
    """@description: Supply a trusted synthetic full-video assertion without a provider.
    @param: none
    @return: Evidence for a three-minute video.
    """
    return DurationEvidence(VIDEO, 180, source="synthetic-fixture", reliable=True, full_video=True)


@pytest.mark.parametrize("seconds, expected", [
    (0, DurationEligibility.REJECT), (59, DurationEligibility.REJECT),
    (60, DurationEligibility.PASS), (61, DurationEligibility.PASS),
    (719, DurationEligibility.PASS), (720, DurationEligibility.PASS),
    (721, DurationEligibility.REJECT), (3600, DurationEligibility.REJECT),
    (10 ** 100, DurationEligibility.REJECT),
    (Decimal("59.999999999999999999"), DurationEligibility.REJECT),
    (Decimal("60"), DurationEligibility.PASS), (Decimal("60.0001"), DurationEligibility.PASS),
    (Decimal("719.9999"), DurationEligibility.PASS), (Decimal("720"), DurationEligibility.PASS),
    (Decimal("720.000000000000000001"), DurationEligibility.REJECT),
])
def test_exact_duration_limits(duration_evidence, seconds, expected):
    """@description: Enforce inclusive limits without rounding away boundary violations.
    @param duration_evidence: Synthetic reliable evidence.
    @param seconds: Exact full-video duration.
    @param expected: Required hard duration result.
    @return: None.
    """
    assert assess_duration(replace(duration_evidence, seconds=seconds)) is expected


@pytest.mark.parametrize("seconds", [
    None, -1, Decimal("-0.1"), True, False, 60.0, float("nan"), float("inf"),
    "60", "PT1M", "PT1000000H", "invalid", "", [], {},
    Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-Infinity"),
])
def test_unassessable_duration_stays_pending(duration_evidence, seconds):
    """@description: Never treat malformed, imprecise or unsupported data as reliable duration.
    @param duration_evidence: Valid baseline evidence.
    @param seconds: Unsupported value, including intentionally unsupported ISO strings.
    @return: None.
    """
    assert assess_duration(replace(duration_evidence, seconds=seconds)) is DurationEligibility.PENDING_VALIDATION


@pytest.mark.parametrize("changes", [
    {"source": None}, {"source": ""}, {"source": "  "}, {"source": 123},
    {"reliable": False}, {"reliable": None}, {"reliable": 1},
    {"full_video": False}, {"full_video": None}, {"full_video": 1},
    {"video": None},
])
@pytest.mark.parametrize("seconds", [59, 180, 721])
def test_missing_evidence_precedes_duration_decision(duration_evidence, changes, seconds):
    """@description: Withhold both pass and rejection when source or scope is not reliable.
    @param duration_evidence: Valid baseline evidence.
    @param changes: Missing provenance, reliability or full-video scope.
    @param seconds: Otherwise passing or failing duration.
    @return: None.
    """
    assert assess_duration(replace(duration_evidence, seconds=seconds, **changes)) is DurationEligibility.PENDING_VALIDATION


def test_default_evidence_is_untrusted():
    """@description: Require deliberate reliability and full-video assertions from future adapters.
    @param: none
    @return: None.
    """
    assert assess_duration(DurationEvidence(VIDEO, 180)) is DurationEligibility.PENDING_VALIDATION
