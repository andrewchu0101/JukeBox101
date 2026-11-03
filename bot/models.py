"""Define small application results shared by persistence and services.

These values carry public outcomes without depending on Discord or database connections.
"""

from dataclasses import dataclass
from datetime import datetime

HISTORY_LIMIT = 10
SUBMISSION_HISTORY_DISPLAY_LIMIT = 5  # [display size]: Beta /history rows shown at once; kept apart from HISTORY_LIMIT, which the repository queries share.
MAX_REPLY_LENGTH = 1900


@dataclass(frozen=True)
class SubmissionResult:
    """Describe a persisted attempt without claiming an award or music verification."""

    resolution_status: str
    duplicate_submission: bool


@dataclass(frozen=True)
class DeliveryHistoryEntry:
    """Expose only the recipient's successful delivery time and verified target."""

    youtube_url: str
    delivered_at: datetime
