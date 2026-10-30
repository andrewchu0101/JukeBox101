-- REVIEW DRAFT ONLY. Not executed against any database. Apply only to an approved, disposable isolated schema
-- after the Owner confirms the target. Requires MySQL 8.0.16 or later for DROP CHECK and CHECK enforcement.
--
-- Rollback limit: once a nickel_* ledger row exists, its reason value cannot be removed without deleting ledger
-- history. Rollback therefore means switching the feature off; ledger rows are never deleted or edited.

ALTER TABLE credit_ledger DROP CHECK chk_ledger_reason;

ALTER TABLE credit_ledger
    MODIFY reason ENUM('submission_award', 'exchange_debit', 'delivery_refund', 'nickel_entry_grant', 'nickel_weekly_grant') NOT NULL;

ALTER TABLE credit_ledger ADD CONSTRAINT chk_ledger_reason CHECK (
    (reason = 'submission_award' AND credit_delta = 1 AND awarded_song_id IS NOT NULL AND recommendation_id IS NULL) OR
    (reason = 'exchange_debit' AND credit_delta = -1 AND awarded_song_id IS NULL AND recommendation_id IS NOT NULL) OR
    (reason = 'delivery_refund' AND credit_delta = 1 AND awarded_song_id IS NULL AND recommendation_id IS NOT NULL) OR
    (reason = 'nickel_entry_grant' AND credit_delta = 2 AND awarded_song_id IS NULL AND recommendation_id IS NULL) OR
    (reason = 'nickel_weekly_grant' AND credit_delta = 1 AND awarded_song_id IS NULL AND recommendation_id IS NULL)
);

-- One row per approved grant. The unique key on (discord_user_id, grant_kind, grant_period) enforces
-- one entry grant per Discord account for life and one weekly grant per account per Taipei week.
CREATE TABLE IF NOT EXISTS nickel_grants (
    grant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    grant_kind ENUM('entry', 'weekly') NOT NULL,
    grant_period VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    amount TINYINT UNSIGNED NOT NULL,
    ledger_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_nickel_grant_per_period UNIQUE (discord_user_id, grant_kind, grant_period),
    CONSTRAINT uq_nickel_grant_ledger UNIQUE (ledger_id),
    CONSTRAINT fk_nickel_grant_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id),
    CONSTRAINT fk_nickel_grant_ledger FOREIGN KEY (ledger_id) REFERENCES credit_ledger (ledger_id),
    CONSTRAINT chk_nickel_grant_shape CHECK (
        (grant_kind = 'entry' AND grant_period = 'LIFETIME' AND amount = 2) OR
        (grant_kind = 'weekly' AND grant_period <> 'LIFETIME' AND amount = 1)
    )
);
