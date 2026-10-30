"""Persist pending submissions and user preferences using the reviewed PostgreSQL schema.

Each synchronous operation owns a complete short transaction in its calling worker context.
"""

from bot.config import DatabaseSettings
from bot.database.connection import transaction
from bot.models import DeliveryHistoryEntry, HISTORY_LIMIT, SubmissionResult
from bot.urls import YouTubeVideo


class MusicRepository:
    """Own SQL operations using validated settings and worker-local connections."""

    def __init__(self, settings: DatabaseSettings) -> None:
        """@description: Retain configuration without opening a connection.
        @param self: Repository instance used by application services.
        @param settings: Validated destination, with secret-safe representation.
        @return: None.
        """
        self.settings = settings  # [connection configuration]: No live connection is retained.

    def submit_pending(self, discord_user_id: int, video: YouTubeVideo) -> SubmissionResult:
        """@description: Record a deduplicated attempt without inventing eligibility or credits.
        @param self: Repository owning transaction boundaries.
        @param discord_user_id: Discord identity submitting the video.
        @param video: Validated source and normalized platform identifier.
        @return: Existing resolution and whether this recipient already submitted it.
        """
        with transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                _ensure_user(cursor, discord_user_id)
                _upsert_pending_song(cursor, video)
                cursor.execute(
                    "SELECT song_id, resolution_status FROM songs "
                    "WHERE platform = %s AND platform_track_id = %s FOR UPDATE",
                    ("youtube", video.video_id),
                )
                # [canonical identity]: The unique video row serializes competing submissions.
                song = cursor.fetchone()
                duplicate_submission = _record_submission(cursor, discord_user_id, song["song_id"])
                return SubmissionResult(song["resolution_status"], duplicate_submission)

    def balance(self, discord_user_id: int) -> int:
        """@description: Distinguish absent users from failed credit queries.
        @param self: Repository owning transaction boundaries.
        @param discord_user_id: Recipient requesting their spendable balance.
        @return: Stored balance, or zero only when the user does not exist.
        """
        with transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT credit_balance FROM users WHERE discord_user_id = %s", (discord_user_id,))
                account = cursor.fetchone()
                return 0 if account is None else account["credit_balance"]

    def set_daily(self, discord_user_id: int, enabled: bool) -> None:
        """@description: Persist an idempotent opt-in preference without scheduling delivery.
        @param self: Repository owning transaction boundaries.
        @param discord_user_id: Recipient choosing their daily preference.
        @param enabled: Desired final preference, independent of previous requests.
        @return: None.
        """
        with transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                _ensure_user(cursor, discord_user_id)
                cursor.execute("UPDATE users SET daily_enabled = %s WHERE discord_user_id = %s", (enabled, discord_user_id))

    def history(self, discord_user_id: int) -> list[DeliveryHistoryEntry]:
        """@description: Bound successful delivery history with a deterministic tie breaker.
        @param self: Repository owning transaction boundaries.
        @param discord_user_id: Recipient whose private history is requested.
        @return: Most recent successful deliveries, without sender identities.
        """
        with transaction(self.settings) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT s.youtube_url, r.delivered_at FROM recommendations r "
                    "JOIN songs s ON s.song_id = r.song_id "
                    "WHERE r.discord_user_id = %s AND r.status = %s "
                    "ORDER BY r.delivered_at DESC, r.recommendation_id DESC LIMIT %s",
                    (discord_user_id, "delivered", HISTORY_LIMIT),
                )
                return [DeliveryHistoryEntry(**delivery) for delivery in cursor.fetchall()]


def _ensure_user(cursor, discord_user_id: int) -> None:
    """@description: Create default account state without resetting an existing balance.
    @param cursor: Cursor owned by the current transaction and worker.
    @param discord_user_id: Account identity protected by the users primary key.
    @return: None.
    """
    cursor.execute(
        "INSERT INTO users (discord_user_id) VALUES (%s) "
        "ON CONFLICT (discord_user_id) DO NOTHING",
        (discord_user_id,),
    )


def _upsert_pending_song(cursor, video: YouTubeVideo) -> None:
    """@description: Preserve first provenance and existing verification on duplicate video IDs.
    @param cursor: Cursor inside the short submission transaction.
    @param video: Parsed submission, with no fabricated metadata.
    @return: None.
    """
    cursor.execute(
        "INSERT INTO songs (platform, platform_track_id, source_url) VALUES (%s, %s, %s) "
        "ON CONFLICT (platform, platform_track_id) DO NOTHING",
        ("youtube", video.video_id, video.source_url),
    )


def _record_submission(cursor, discord_user_id: int, canonical_song_id: int) -> bool:
    """@description: Record one attempt per user and song under the locked song row.
    @param cursor: Cursor holding the canonical song lock.
    @param discord_user_id: Submitter whose attempt is being recorded.
    @param canonical_song_id: Internal identity returned by the unique platform row.
    @return: Whether the same user already has an attempt for this song.
    """
    cursor.execute(
        "SELECT submission_id FROM submissions WHERE discord_user_id = %s AND song_id = %s",
        (discord_user_id, canonical_song_id),
    )
    if cursor.fetchone() is not None:
        return True
    cursor.execute(
        "INSERT INTO submissions (discord_user_id, song_id) VALUES (%s, %s)",
        (discord_user_id, canonical_song_id),
    )
    return False
