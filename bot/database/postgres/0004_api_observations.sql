-- JukeBox101 PostgreSQL schema, version 0004: short-lived store for values obtained from the YouTube Data API.
-- Additive only: no existing table is altered, no row is rewritten, and no existing privilege changes.
-- The CHECK constraints bound the stored columns (the expiry window, and no values once purged). They never remove anything:
-- expired rows stay until the retention job (python -m bot.api_retention, also run by the Bot) sets their values to NULL.
-- The application role may UPDATE (purge) but never DELETE these tables, and still cannot UPDATE any history table.

CREATE TABLE jb2_api_observations (
    video_id VARCHAR(11) COLLATE "C" NOT NULL PRIMARY KEY REFERENCES jb2_videos (video_id),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    duration_seconds INTEGER,
    channel_id VARCHAR(64) COLLATE "C",
    purged_at TIMESTAMPTZ,
    purge_reason VARCHAR(32) COLLATE "C",
    CONSTRAINT chk_jb2_api_observation_window CHECK (expires_at > observed_at AND expires_at <= observed_at + INTERVAL '30 days'),
    CONSTRAINT chk_jb2_api_observation_purge_reason CHECK ((purged_at IS NULL) = (purge_reason IS NULL)),
    CONSTRAINT chk_jb2_api_observation_purge_reason_value CHECK (purge_reason IS NULL OR purge_reason IN ('expired', 'completed', 'unavailable')),
    CONSTRAINT chk_jb2_api_observation_purged_values CHECK (purged_at IS NULL OR (duration_seconds IS NULL AND channel_id IS NULL)),
    CONSTRAINT chk_jb2_api_observation_live_values CHECK (purged_at IS NOT NULL OR duration_seconds IS NOT NULL)
);
CREATE INDEX ix_jb2_api_observations_unpurged_expiry ON jb2_api_observations (expires_at) WHERE purged_at IS NULL;

CREATE TABLE jb2_retention_runs (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
    finished_at TIMESTAMPTZ,
    purged_rows INTEGER NOT NULL DEFAULT 0,
    error_class VARCHAR(64) COLLATE "C",
    CONSTRAINT chk_jb2_retention_run_counts CHECK (purged_rows >= 0)
);

GRANT SELECT, INSERT, UPDATE ON jb2_api_observations TO jukebox101_app_role;
GRANT SELECT, INSERT ON jb2_retention_runs TO jukebox101_app_role;
GRANT UPDATE (finished_at, purged_rows, error_class) ON jb2_retention_runs TO jukebox101_app_role;
GRANT SELECT ON jb2_api_observations, jb2_retention_runs TO jukebox101_readonly_role;
