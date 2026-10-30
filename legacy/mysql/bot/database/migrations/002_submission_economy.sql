-- Reviewed plan only. NEVER automatically run at startup.
-- MySQL 8.0.16+, InnoDB, UTC. DDL implicitly commits; see Batch B acceptance runbook.
-- The guarded Python migration runner owns version/checksum bookkeeping.
CREATE TABLE IF NOT EXISTS jb2_control (
    singleton_id TINYINT NOT NULL PRIMARY KEY,
    schema_version VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    accepted_sequence BIGINT UNSIGNED NOT NULL DEFAULT 0,
    revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    snapshot_ready BOOLEAN NOT NULL DEFAULT FALSE,
    legacy_writers_quiesced BOOLEAN NOT NULL DEFAULT FALSE,
    settlement_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT chk_jb2_singleton CHECK (singleton_id = 1),
    CONSTRAINT chk_jb2_switches CHECK (snapshot_ready IN (0,1) AND legacy_writers_quiesced IN (0,1) AND settlement_enabled IN (0,1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_versions (
    version_id VARCHAR(128) NOT NULL PRIMARY KEY,
    canonical_id VARCHAR(128) NOT NULL,
    evidence_reference VARCHAR(128) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX ix_jb2_version_root (canonical_id),
    CONSTRAINT fk_jb2_version_root FOREIGN KEY (canonical_id) REFERENCES jb2_versions(version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_videos (
    video_id VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    song_id BIGINT UNSIGNED NOT NULL UNIQUE,
    identity_json JSON NOT NULL,
    review_json JSON NOT NULL,
    review_revision BIGINT UNSIGNED NOT NULL,
    review_status VARCHAR(32) NOT NULL,
    CONSTRAINT fk_jb2_video_song FOREIGN KEY (song_id) REFERENCES songs(song_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_legacy (
    song_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    identity_json JSON NULL,
    CONSTRAINT fk_jb2_legacy_song FOREIGN KEY (song_id) REFERENCES songs(song_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_events (
    operation_id VARCHAR(128) NOT NULL PRIMARY KEY,
    accepted_sequence BIGINT UNSIGNED NOT NULL UNIQUE,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    video_id VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    accepted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    reward_status VARCHAR(32) NOT NULL,
    conflict_frozen BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT fk_jb2_event_user FOREIGN KEY (discord_user_id) REFERENCES users(discord_user_id),
    CONSTRAINT fk_jb2_event_video FOREIGN KEY (video_id) REFERENCES jb2_videos(video_id),
    CONSTRAINT chk_jb2_freeze CHECK (conflict_frozen IN (0,1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_receipts (
    operation_id VARCHAR(128) NOT NULL PRIMARY KEY,
    receipt_json JSON NOT NULL,
    CONSTRAINT fk_jb2_receipt_event FOREIGN KEY (operation_id) REFERENCES jb2_events(operation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_claims (
    version_id VARCHAR(128) NOT NULL PRIMARY KEY,
    operation_id VARCHAR(128) NOT NULL UNIQUE,
    ledger_id BIGINT UNSIGNED NOT NULL UNIQUE,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_jb2_claim_version FOREIGN KEY (version_id) REFERENCES jb2_versions(version_id),
    CONSTRAINT fk_jb2_claim_event FOREIGN KEY (operation_id) REFERENCES jb2_events(operation_id),
    CONSTRAINT fk_jb2_claim_ledger FOREIGN KEY (ledger_id) REFERENCES credit_ledger(ledger_id),
    CONSTRAINT fk_jb2_claim_user FOREIGN KEY (discord_user_id) REFERENCES users(discord_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_settlements (
    operation_id VARCHAR(128) NOT NULL PRIMARY KEY,
    result_json JSON NOT NULL,
    CONSTRAINT fk_jb2_settlement_event FOREIGN KEY (operation_id) REFERENCES jb2_events(operation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_review_history (
    video_id VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    revision BIGINT UNSIGNED NOT NULL,
    review_json JSON NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    decision_reason VARCHAR(1024) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (video_id, revision),
    CONSTRAINT fk_jb2_review_video FOREIGN KEY (video_id) REFERENCES jb2_videos(video_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_evidence (
    evidence_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    video_id VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    evidence_json JSON NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    decision_reason VARCHAR(1024) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_jb2_evidence_video FOREIGN KEY (video_id) REFERENCES jb2_videos(video_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_decisions (
    decision_id VARCHAR(128) NOT NULL PRIMARY KEY,
    request_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    decision_reason VARCHAR(1024) NOT NULL,
    result_json JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_exclusions (
    evidence_reference VARCHAR(128) NOT NULL PRIMARY KEY,
    video_id VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version_id VARCHAR(128) NOT NULL,
    evidence_json JSON NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    decision_reason VARCHAR(1024) NOT NULL,
    CONSTRAINT fk_jb2_exclusion_version FOREIGN KEY (version_id) REFERENCES jb2_versions(version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_barriers (
    version_id VARCHAR(128) NOT NULL PRIMARY KEY,
    reason VARCHAR(1024) NOT NULL,
    CONSTRAINT fk_jb2_barrier_version FOREIGN KEY (version_id) REFERENCES jb2_versions(version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_exceptions (
    exception_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    audit_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    version_id VARCHAR(128) NOT NULL,
    actor_id BIGINT UNSIGNED NOT NULL,
    reason VARCHAR(1024) NOT NULL,
    original_claims JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_jb2_exception_version FOREIGN KEY (version_id) REFERENCES jb2_versions(version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_reminders (
    video_id VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    next_due_at DATETIME(6) NOT NULL,
    last_summary_date DATE NULL,
    CONSTRAINT fk_jb2_reminder_video FOREIGN KEY (video_id) REFERENCES jb2_videos(video_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS jb2_reminder_batches (
    summary_date DATE NOT NULL PRIMARY KEY,
    owner_discord_user_id BIGINT UNSIGNED NOT NULL,
    operation_ids JSON NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
