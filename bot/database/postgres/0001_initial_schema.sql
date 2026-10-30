-- JukeBox101 PostgreSQL schema, version 0001 (PostgreSQL 18; verified on 18.6).
-- Applied only by `python -m bot.migrate apply` as the schema-owner (migrator) role, inside one transaction.
-- This is a fresh schema for an empty database. It is not a translation run against MySQL data and does not
-- reuse the MySQL migration numbers 002-005; their reviewed constraints are folded in below.
--
-- Conventions:
--   * Discord snowflakes and other platform IDs are BIGINT with CHECK (> 0); they are external identifiers, never
--     generated here. Values must fit PostgreSQL BIGINT (< 2^63); the application refuses larger IDs before writing.
--   * Database-generated identifiers use GENERATED ALWAYS AS IDENTITY; they are never business order. Intake order
--     is jb2_control.accepted_sequence, allocated under the control row lock.
--   * Timestamps are TIMESTAMPTZ (UTC session); identifier text uses COLLATE "C" (byte-wise, case-sensitive).
--   * Former MySQL ENUMs are TEXT with named CHECK constraints; former JSON columns are json (stored text kept verbatim).

CREATE TABLE users (
    discord_user_id BIGINT NOT NULL PRIMARY KEY,
    credit_balance BIGINT NOT NULL DEFAULT 0,
    daily_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT chk_users_id CHECK (discord_user_id > 0),
    CONSTRAINT chk_users_balance CHECK (credit_balance >= 0)
);

CREATE TABLE songs (
    song_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    platform VARCHAR(16) COLLATE "C" NOT NULL DEFAULT 'youtube',
    platform_track_id VARCHAR(11) COLLATE "C" NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    youtube_url VARCHAR(2048) NULL,
    resolution_status TEXT NOT NULL DEFAULT 'pending',
    verification_provenance TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_songs_platform_track UNIQUE (platform, platform_track_id),
    CONSTRAINT chk_songs_platform CHECK (platform = 'youtube'),
    CONSTRAINT chk_songs_track CHECK (platform_track_id ~ '^[A-Za-z0-9_-]{11}$'),
    CONSTRAINT chk_songs_status CHECK (resolution_status IN ('pending', 'verified', 'rejected')),
    CONSTRAINT chk_songs_verified CHECK (
        resolution_status <> 'verified' OR (youtube_url IS NOT NULL AND verification_provenance IS NOT NULL)
    )
);

CREATE TABLE submissions (
    -- Deduplicated legacy submission attempts (one row per user/song); not accepted submissions or awarded credits.
    submission_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    song_id BIGINT NOT NULL REFERENCES songs (song_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_submissions_user_song UNIQUE (discord_user_id, song_id)
);
CREATE INDEX ix_submissions_song ON submissions (song_id);

CREATE TABLE recommendations (
    recommendation_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    song_id BIGINT NOT NULL REFERENCES songs (song_id),
    status TEXT NOT NULL DEFAULT 'reserved',
    discord_message_id BIGINT NULL,
    delivered_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    claim_token CHAR(32) COLLATE "C" NULL,
    claimed_at TIMESTAMPTZ NULL,
    released_at TIMESTAMPTZ NULL,
    -- Exact unique targets for daily ownership and ledger recipient foreign keys.
    CONSTRAINT uq_recommendations_owner UNIQUE (recommendation_id, discord_user_id, song_id),
    CONSTRAINT uq_recommendations_recipient UNIQUE (recommendation_id, discord_user_id),
    CONSTRAINT chk_recommendations_status CHECK (status IN ('reserved', 'sending', 'delivered', 'failed', 'uncertain')),
    CONSTRAINT chk_recommendations_message CHECK (discord_message_id IS NULL OR discord_message_id > 0),
    CONSTRAINT chk_recommendations_delivery CHECK (
        (status = 'delivered' AND delivered_at IS NOT NULL) OR (status <> 'delivered' AND delivered_at IS NULL)
    ),
    CONSTRAINT chk_recommendations_sending_token CHECK (status <> 'sending' OR (claim_token IS NOT NULL AND claimed_at IS NOT NULL)),
    CONSTRAINT chk_recommendations_release CHECK (released_at IS NULL OR status = 'failed')
);
-- A released reservation no longer occupies its (user, song) slot (former MySQL generated column active_slot).
CREATE UNIQUE INDEX uq_recommendations_user_song_active ON recommendations (discord_user_id, song_id) WHERE released_at IS NULL;
CREATE INDEX ix_recommendations_history ON recommendations (discord_user_id, delivered_at);
CREATE INDEX ix_recommendations_song ON recommendations (song_id);

-- Former MySQL "ON UPDATE CURRENT_TIMESTAMP(6)".
CREATE FUNCTION jb_touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := statement_timestamp();
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_recommendations_updated_at BEFORE UPDATE ON recommendations
    FOR EACH ROW EXECUTE FUNCTION jb_touch_updated_at();

CREATE TABLE credit_ledger (
    ledger_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    credit_delta BIGINT NOT NULL,
    reason TEXT NOT NULL,
    idempotency_key VARCHAR(128) COLLATE "C" NOT NULL,
    awarded_song_id BIGINT NULL REFERENCES songs (song_id),
    recommendation_id BIGINT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_ledger_idempotency UNIQUE (idempotency_key),
    CONSTRAINT uq_ledger_song_award UNIQUE (awarded_song_id),
    CONSTRAINT uq_ledger_recommendation_reason UNIQUE (recommendation_id, reason),
    CONSTRAINT fk_ledger_recommendation FOREIGN KEY (recommendation_id, discord_user_id)
        REFERENCES recommendations (recommendation_id, discord_user_id),
    CONSTRAINT chk_ledger_reason CHECK (
        (reason = 'submission_award' AND credit_delta = 1 AND awarded_song_id IS NOT NULL AND recommendation_id IS NULL) OR
        (reason = 'exchange_debit' AND credit_delta = -1 AND awarded_song_id IS NULL AND recommendation_id IS NOT NULL) OR
        (reason = 'delivery_refund' AND credit_delta = 1 AND awarded_song_id IS NULL AND recommendation_id IS NOT NULL) OR
        (reason = 'nickel_entry_grant' AND credit_delta = 2 AND awarded_song_id IS NULL AND recommendation_id IS NULL) OR
        (reason = 'nickel_weekly_grant' AND credit_delta = 1 AND awarded_song_id IS NULL AND recommendation_id IS NULL)
    )
);
CREATE INDEX ix_ledger_history ON credit_ledger (discord_user_id, created_at);

CREATE TABLE daily_dispatches (
    dispatch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    song_id BIGINT NULL,
    recommendation_id BIGINT NULL,
    dispatch_date DATE NOT NULL, -- Asia/Taipei calendar date (daily delivery is not implemented).
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code VARCHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_dispatch_user_date UNIQUE (discord_user_id, dispatch_date),
    CONSTRAINT uq_dispatch_recommendation UNIQUE (recommendation_id),
    CONSTRAINT fk_dispatch_owner FOREIGN KEY (recommendation_id, discord_user_id, song_id)
        REFERENCES recommendations (recommendation_id, discord_user_id, song_id),
    CONSTRAINT chk_dispatch_status CHECK (status IN ('pending', 'sending', 'delivered', 'failed', 'uncertain')),
    CONSTRAINT chk_dispatch_attempts CHECK (attempt_count >= 0),
    CONSTRAINT chk_dispatch_reservation CHECK (
        (status = 'pending' AND song_id IS NULL AND recommendation_id IS NULL) OR
        (status <> 'pending' AND song_id IS NOT NULL AND recommendation_id IS NOT NULL)
    )
);
CREATE INDEX ix_dispatch_recovery ON daily_dispatches (status, dispatch_date);
CREATE TRIGGER trg_daily_dispatches_updated_at BEFORE UPDATE ON daily_dispatches
    FOR EACH ROW EXECUTE FUNCTION jb_touch_updated_at();

-- Submission economy (former MySQL migration 002).
CREATE TABLE jb2_control (
    singleton_id SMALLINT NOT NULL PRIMARY KEY,
    schema_version VARCHAR(32) COLLATE "C" NOT NULL,
    accepted_sequence BIGINT NOT NULL DEFAULT 0,
    revision BIGINT NOT NULL DEFAULT 0,
    snapshot_ready BOOLEAN NOT NULL DEFAULT FALSE,
    legacy_writers_quiesced BOOLEAN NOT NULL DEFAULT FALSE,
    settlement_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT chk_jb2_singleton CHECK (singleton_id = 1),
    CONSTRAINT chk_jb2_counters CHECK (accepted_sequence >= 0 AND revision >= 0)
);

CREATE TABLE jb2_versions (
    version_id VARCHAR(128) COLLATE "C" NOT NULL PRIMARY KEY,
    canonical_id VARCHAR(128) COLLATE "C" NOT NULL REFERENCES jb2_versions (version_id),
    evidence_reference VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX ix_jb2_version_root ON jb2_versions (canonical_id);

CREATE TABLE jb2_videos (
    video_id VARCHAR(11) COLLATE "C" NOT NULL PRIMARY KEY,
    song_id BIGINT NOT NULL UNIQUE REFERENCES songs (song_id),
    identity_json json NOT NULL,
    review_json json NOT NULL,
    review_revision BIGINT NOT NULL,
    review_status VARCHAR(32) NOT NULL,
    CONSTRAINT chk_jb2_video_id CHECK (video_id ~ '^[A-Za-z0-9_-]{11}$'),
    CONSTRAINT chk_jb2_video_revision CHECK (review_revision >= 0)
);

CREATE TABLE jb2_legacy (
    song_id BIGINT NOT NULL PRIMARY KEY REFERENCES songs (song_id),
    identity_json json NULL
);

CREATE TABLE jb2_events (
    operation_id VARCHAR(128) COLLATE "C" NOT NULL PRIMARY KEY,
    accepted_sequence BIGINT NOT NULL UNIQUE,
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    video_id VARCHAR(11) COLLATE "C" NOT NULL REFERENCES jb2_videos (video_id),
    source_url VARCHAR(2048) NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    reward_status VARCHAR(32) NOT NULL,
    conflict_frozen BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT chk_jb2_event_sequence CHECK (accepted_sequence > 0)
);
CREATE INDEX ix_jb2_events_user_sequence ON jb2_events (discord_user_id, accepted_sequence DESC);
CREATE INDEX ix_jb2_events_video ON jb2_events (video_id);

CREATE TABLE jb2_receipts (
    operation_id VARCHAR(128) COLLATE "C" NOT NULL PRIMARY KEY REFERENCES jb2_events (operation_id),
    receipt_json json NOT NULL
);

CREATE TABLE jb2_claims (
    version_id VARCHAR(128) COLLATE "C" NOT NULL PRIMARY KEY REFERENCES jb2_versions (version_id),
    operation_id VARCHAR(128) COLLATE "C" NOT NULL UNIQUE REFERENCES jb2_events (operation_id),
    ledger_id BIGINT NOT NULL UNIQUE REFERENCES credit_ledger (ledger_id),
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE jb2_settlements (
    operation_id VARCHAR(128) COLLATE "C" NOT NULL PRIMARY KEY REFERENCES jb2_events (operation_id),
    result_json json NOT NULL
);

CREATE TABLE jb2_review_history (
    video_id VARCHAR(11) COLLATE "C" NOT NULL REFERENCES jb2_videos (video_id),
    revision BIGINT NOT NULL,
    review_json json NOT NULL,
    actor_id BIGINT NOT NULL,
    decision_reason VARCHAR(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (video_id, revision)
);

CREATE TABLE jb2_evidence (
    evidence_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    video_id VARCHAR(11) COLLATE "C" NOT NULL REFERENCES jb2_videos (video_id),
    evidence_json json NOT NULL,
    actor_id BIGINT NOT NULL,
    decision_reason VARCHAR(1024) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX ix_jb2_evidence_video ON jb2_evidence (video_id, evidence_id);

CREATE TABLE jb2_decisions (
    decision_id VARCHAR(128) COLLATE "C" NOT NULL PRIMARY KEY,
    request_digest CHAR(64) COLLATE "C" NOT NULL,
    actor_id BIGINT NOT NULL,
    decision_reason VARCHAR(1024) NOT NULL,
    result_json json NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

CREATE TABLE jb2_exclusions (
    evidence_reference VARCHAR(128) COLLATE "C" NOT NULL PRIMARY KEY,
    video_id VARCHAR(11) COLLATE "C" NOT NULL,
    version_id VARCHAR(128) COLLATE "C" NOT NULL REFERENCES jb2_versions (version_id),
    evidence_json json NOT NULL,
    actor_id BIGINT NOT NULL,
    decision_reason VARCHAR(1024) NOT NULL
);
CREATE INDEX ix_jb2_exclusions_version ON jb2_exclusions (version_id);

CREATE TABLE jb2_barriers (
    version_id VARCHAR(128) COLLATE "C" NOT NULL PRIMARY KEY REFERENCES jb2_versions (version_id),
    reason VARCHAR(1024) NOT NULL
);

CREATE TABLE jb2_exceptions (
    exception_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    audit_key CHAR(64) COLLATE "C" NOT NULL UNIQUE,
    version_id VARCHAR(128) COLLATE "C" NOT NULL REFERENCES jb2_versions (version_id),
    actor_id BIGINT NOT NULL,
    reason VARCHAR(1024) NOT NULL,
    original_claims json NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX ix_jb2_exceptions_version ON jb2_exceptions (version_id);

CREATE TABLE jb2_reminders (
    video_id VARCHAR(11) COLLATE "C" NOT NULL PRIMARY KEY REFERENCES jb2_videos (video_id),
    next_due_at TIMESTAMPTZ NOT NULL,
    last_summary_date DATE NULL
);

CREATE TABLE jb2_reminder_batches (
    summary_date DATE NOT NULL PRIMARY KEY,
    owner_discord_user_id BIGINT NOT NULL,
    operation_ids json NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);

-- Nickel grants (former MySQL migrations 004 and 005). The /claim feature stays off unless explicitly enabled.
CREATE TABLE nickel_grants (
    grant_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    grant_kind TEXT NOT NULL,
    grant_period VARCHAR(16) COLLATE "C" NOT NULL,
    amount SMALLINT NOT NULL,
    ledger_id BIGINT NOT NULL REFERENCES credit_ledger (ledger_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    CONSTRAINT uq_nickel_grant_per_period UNIQUE (discord_user_id, grant_kind, grant_period),
    CONSTRAINT uq_nickel_grant_ledger UNIQUE (ledger_id),
    CONSTRAINT chk_nickel_grant_kind CHECK (grant_kind IN ('entry', 'weekly')),
    CONSTRAINT chk_nickel_grant_shape CHECK (
        (grant_kind = 'entry' AND grant_period = 'LIFETIME' AND amount = 2) OR
        (grant_kind = 'weekly' AND grant_period <> 'LIFETIME' AND amount = 1)
    )
);

CREATE TABLE nickel_claim_requests (
    request_key VARCHAR(64) COLLATE "C" NOT NULL PRIMARY KEY,
    discord_user_id BIGINT NOT NULL REFERENCES users (discord_user_id),
    grant_period VARCHAR(16) COLLATE "C" NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
);
CREATE INDEX ix_nickel_claim_requests_user ON nickel_claim_requests (discord_user_id);
