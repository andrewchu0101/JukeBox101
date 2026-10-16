-- Phase 1 v1: manual review and execution only, MySQL >= 8.0.16.
-- Select jukebox101 in Workbench first. No database/account creation.
-- IF NOT EXISTS does not validate or repair existing tables.
-- All DATETIME values use UTC; dispatch_date uses Asia/Taipei calendar dates.
-- Phase 1A review: this file is not a migration for existing tables.
-- YouTube-only: VARCHAR(11) stores a case-sensitive Video ID, not a musical work ID.
-- Spotify requires a reviewed migration of ID width, platform/length checks and links.

CREATE TABLE IF NOT EXISTS users (
    discord_user_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    credit_balance BIGINT NOT NULL DEFAULT 0,
    daily_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT chk_users_balance CHECK (credit_balance >= 0),
    CONSTRAINT chk_users_daily CHECK (daily_enabled IN (0, 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS songs (
    song_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    platform VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'youtube',
    platform_track_id VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    youtube_url VARCHAR(2048) NULL,
    resolution_status ENUM('pending', 'verified', 'rejected') NOT NULL DEFAULT 'pending',
    verification_provenance TEXT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_songs_platform_track UNIQUE (platform, platform_track_id),
    CONSTRAINT chk_songs_platform CHECK (platform = 'youtube'),
    CONSTRAINT chk_songs_track CHECK (CHAR_LENGTH(platform_track_id) = 11),
    CONSTRAINT chk_songs_verified CHECK (
        resolution_status <> 'verified' OR
        (youtube_url IS NOT NULL AND verification_provenance IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS submissions (
    -- Deduplicated submission attempts, not accepted submissions or awarded credits.
    -- One row per user/song; repeated attempts do not create additional rows.
    -- Only verified eligible songs may earn the single global ledger award.
    submission_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    song_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_submissions_user_song UNIQUE (discord_user_id, song_id),
    CONSTRAINT fk_submissions_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id),
    CONSTRAINT fk_submissions_song FOREIGN KEY (song_id) REFERENCES songs (song_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS recommendations (
    recommendation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    song_id BIGINT UNSIGNED NOT NULL,
    status ENUM('reserved', 'sending', 'delivered', 'failed', 'uncertain') NOT NULL DEFAULT 'reserved',
    discord_message_id BIGINT UNSIGNED NULL,
    delivered_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_recommendations_user_song UNIQUE (discord_user_id, song_id),
    -- Exact unique targets for daily ownership and ledger recipient foreign keys.
    -- These overlap the primary key but serve different composite FK contracts.
    CONSTRAINT uq_recommendations_owner UNIQUE (recommendation_id, discord_user_id, song_id),
    CONSTRAINT uq_recommendations_recipient UNIQUE (recommendation_id, discord_user_id),
    CONSTRAINT fk_recommendations_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id),
    CONSTRAINT fk_recommendations_song FOREIGN KEY (song_id) REFERENCES songs (song_id),
    CONSTRAINT chk_recommendations_delivery CHECK (
        (status = 'delivered' AND delivered_at IS NOT NULL) OR
        (status <> 'delivered' AND delivered_at IS NULL)
    ),
    INDEX ix_recommendations_history (discord_user_id, delivered_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS credit_ledger (
    ledger_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    credit_delta BIGINT NOT NULL,
    reason ENUM('submission_award', 'exchange_debit', 'delivery_refund') NOT NULL,
    idempotency_key VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    awarded_song_id BIGINT UNSIGNED NULL,
    recommendation_id BIGINT UNSIGNED NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_ledger_idempotency UNIQUE (idempotency_key),
    CONSTRAINT uq_ledger_song_award UNIQUE (awarded_song_id),
    CONSTRAINT uq_ledger_recommendation_reason UNIQUE (recommendation_id, reason),
    CONSTRAINT fk_ledger_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id),
    CONSTRAINT fk_ledger_award FOREIGN KEY (awarded_song_id) REFERENCES songs (song_id),
    CONSTRAINT fk_ledger_recommendation FOREIGN KEY (recommendation_id, discord_user_id)
        REFERENCES recommendations (recommendation_id, discord_user_id),
    CONSTRAINT chk_ledger_reason CHECK (
        (reason = 'submission_award' AND credit_delta = 1 AND awarded_song_id IS NOT NULL AND recommendation_id IS NULL) OR
        (reason = 'exchange_debit' AND credit_delta = -1 AND awarded_song_id IS NULL AND recommendation_id IS NOT NULL) OR
        (reason = 'delivery_refund' AND credit_delta = 1 AND awarded_song_id IS NULL AND recommendation_id IS NOT NULL)
    ),
    INDEX ix_ledger_history (discord_user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS daily_dispatches (
    dispatch_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    song_id BIGINT UNSIGNED NULL,
    recommendation_id BIGINT UNSIGNED NULL,
    dispatch_date DATE NOT NULL,
    status ENUM('pending', 'sending', 'delivered', 'failed', 'uncertain') NOT NULL DEFAULT 'pending',
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    last_error_code VARCHAR(64) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_dispatch_user_date UNIQUE (discord_user_id, dispatch_date),
    CONSTRAINT uq_dispatch_recommendation UNIQUE (recommendation_id),
    CONSTRAINT fk_dispatch_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id),
    -- Composite ownership already validates song existence through recommendations.
    CONSTRAINT fk_dispatch_owner FOREIGN KEY (recommendation_id, discord_user_id, song_id)
        REFERENCES recommendations (recommendation_id, discord_user_id, song_id),
    CONSTRAINT chk_dispatch_reservation CHECK (
        (status = 'pending' AND song_id IS NULL AND recommendation_id IS NULL) OR
        (status <> 'pending' AND song_id IS NOT NULL AND recommendation_id IS NOT NULL)
    ),
    INDEX ix_dispatch_recovery (status, dispatch_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
