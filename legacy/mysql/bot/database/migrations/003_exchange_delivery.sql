-- CANDIDATE ONLY. NOT EXECUTED. NOT REGISTERED with the migration runner.
-- Requires separate Owner approval, DDL authorization, and a reviewed run against an approved disposable target.
-- Engine behaviour (generated column, unique key on it, CHECK enforcement) is NOT verified by any run yet;
-- the offline tests use an in-memory fake that does not enforce these constraints.
-- Minimum engine: MySQL 8.0.16 or later (CHECK constraints are enforced from that version).
-- DDL implicitly commits and cannot be rolled back; run only as one reviewed step on the approved target.
-- Adds the delivery lease, the release marker and a release-aware uniqueness key for exchange reservations.
-- Existing reserved, sending, delivered, failed and uncertain rows are kept unchanged; no row is deleted or rewritten.

ALTER TABLE recommendations
    ADD COLUMN claim_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    ADD COLUMN claimed_at DATETIME(6) NULL,
    ADD COLUMN released_at DATETIME(6) NULL;

ALTER TABLE recommendations
    ADD COLUMN active_slot TINYINT GENERATED ALWAYS AS (IF(released_at IS NULL, 1, NULL)) STORED;

-- A released reservation no longer occupies its (user, song) slot, so the same song may be reserved again.
ALTER TABLE recommendations
    DROP INDEX uq_recommendations_user_song,
    ADD CONSTRAINT uq_recommendations_user_song_active UNIQUE (discord_user_id, song_id, active_slot),
    ADD CONSTRAINT chk_recommendations_sending_token CHECK (status <> 'sending' OR (claim_token IS NOT NULL AND claimed_at IS NOT NULL)),
    ADD CONSTRAINT chk_recommendations_release CHECK (released_at IS NULL OR status = 'failed');
