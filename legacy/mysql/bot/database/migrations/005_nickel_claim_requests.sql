-- REVIEW DRAFT ONLY. Not executed against any production database. Applies after 004_nickel_grants.sql.
-- One row per /claim request key. A retry of the same request key replays the original period and grants,
-- so a request that crosses a Taipei week boundary is never mistaken for a new week's claim.
CREATE TABLE IF NOT EXISTS nickel_claim_requests (
    request_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    grant_period VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_nickel_claim_user FOREIGN KEY (discord_user_id) REFERENCES users (discord_user_id)
);
