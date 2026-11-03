-- JukeBox101 PostgreSQL schema, version 0005: scheduled daily recommendation (stage C).
-- Additive only: no existing table is altered, no row is rewritten, nothing is dropped, and no existing privilege changes.
-- A build without this migration still reads users.daily_enabled and daily_dispatches, so rolling the image back needs no schema step.
-- Per user and Taipei day there is at most one task row across daily_dispatches (reservation tasks, unique per user and date since 0001)
-- and daily_notices (not-enough and no-candidate messages); the Bot checks both inside one serialized transaction.

CREATE TABLE daily_preferences (
    discord_user_id BIGINT NOT NULL PRIMARY KEY REFERENCES users (discord_user_id),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    delivery_hour SMALLINT NOT NULL DEFAULT 20,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT chk_daily_preferences_hour CHECK (delivery_hour BETWEEN 0 AND 23)
);

CREATE TABLE daily_notices (
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    notice_day DATE NOT NULL, -- Asia/Taipei calendar date.
    kind TEXT NOT NULL,
    delivery TEXT NOT NULL,
    consecutive_days INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (discord_user_id, notice_day),
    CONSTRAINT chk_daily_notices_kind CHECK (kind IN ('insufficient_credit', 'no_candidate')),
    CONSTRAINT chk_daily_notices_delivery CHECK (delivery IN ('sent', 'failed', 'uncertain', 'silenced')),
    CONSTRAINT chk_daily_notices_streak CHECK (consecutive_days >= 1)
);

-- 0002 never granted daily_dispatches because nothing used it; the application now inserts and updates it, never deletes.
GRANT SELECT, INSERT, UPDATE ON daily_preferences, daily_notices, daily_dispatches TO jukebox101_app_role;
GRANT SELECT ON daily_preferences, daily_notices TO jukebox101_readonly_role;
