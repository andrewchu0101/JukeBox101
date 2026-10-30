-- CANDIDATE ONLY. NOT EXECUTED. NOT REGISTERED with the migration runner. Requires separate Owner approval for DDL and a reviewed
-- run against an approved disposable target. DDL implicitly commits and cannot be rolled back.
-- Mirrors the columns, unique keys and named CHECK constraints listed in bot/database/compensation_schema.py (SCHEMA_SPEC) and exercised
-- offline by tests/compensation_sqlite.py. Engine behaviour is NOT verified by any run yet.
-- Adds three tables only. It does not change credit_ledger.reason or recommendations.status, and it writes no zero-amount ledger row.
-- Enabling the feature requires these tables and the named constraints; with the switch off (the default) nothing reads them.
-- A missing table, column, unique key or named CHECK refuses explicitly. There is no per-user quota or time window.
-- Refund and redelivery are separate decisions: refund_status records the one refund, redelivery_decision the Owner's grant decision.
-- Minimum engine: MySQL 8.0.16 or later (CHECK constraints are enforced from that version).

CREATE TABLE exchange_incidents (
    incident_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    recommendation_id BIGINT UNSIGNED NOT NULL,
    debit_ledger_id BIGINT UNSIGNED NOT NULL,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    song_id BIGINT UNSIGNED NOT NULL,
    reason_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    approved_by BIGINT UNSIGNED NOT NULL,
    approved_at DATETIME(6) NOT NULL,
    state ENUM('approved', 'settled', 'void', 'not_system_fault', 'non_fault_refunded') NOT NULL,
    refund_status ENUM('none', 'refunded_once') NOT NULL DEFAULT 'none',
    refund_ledger_id BIGINT UNSIGNED NULL,
    redelivery_decision ENUM('pending', 'approved', 'not_approved') NOT NULL DEFAULT 'pending',
    settled_at DATETIME(6) NULL,
    closure ENUM('refund_only', 'refund_and_redelivery', 'non_fault_refund') NULL,
    closed_at DATETIME(6) NULL,
    CONSTRAINT uq_incidents_recommendation UNIQUE (recommendation_id),
    CONSTRAINT uq_incidents_debit UNIQUE (debit_ledger_id),
    CONSTRAINT uq_incidents_refund UNIQUE (refund_ledger_id),
    CONSTRAINT fk_incidents_recommendation FOREIGN KEY (recommendation_id) REFERENCES recommendations (recommendation_id),
    CONSTRAINT fk_incidents_debit FOREIGN KEY (debit_ledger_id) REFERENCES credit_ledger (ledger_id),
    CONSTRAINT fk_incidents_refund FOREIGN KEY (refund_ledger_id) REFERENCES credit_ledger (ledger_id),
    CONSTRAINT chk_incidents_refund CHECK ((refund_status = 'refunded_once') = (refund_ledger_id IS NOT NULL)),
    CONSTRAINT chk_incidents_settled CHECK (state <> 'settled' OR (refund_status = 'refunded_once' AND settled_at IS NOT NULL
        AND redelivery_decision <> 'pending' AND closure IS NOT NULL)),
    CONSTRAINT chk_incidents_non_fault CHECK (state <> 'non_fault_refunded' OR (refund_status = 'refunded_once' AND closure = 'non_fault_refund'
        AND redelivery_decision = 'not_approved' AND settled_at IS NOT NULL AND closed_at IS NOT NULL)),
    CONSTRAINT chk_incidents_closed CHECK (closed_at IS NULL OR state IN ('settled', 'void', 'non_fault_refunded'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE exchange_redelivery_grants (
    grant_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    incident_id BIGINT UNSIGNED NOT NULL,
    discord_user_id BIGINT UNSIGNED NOT NULL,
    song_id BIGINT UNSIGNED NOT NULL,
    granted_at DATETIME(6) NOT NULL,  -- audit time of issuance only; no quota uses it
    state ENUM('open', 'claimed', 'sent', 'not_sent', 'uncertain') NOT NULL,
    claim_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    claimed_at DATETIME(6) NULL,
    delivery_fact ENUM('none', 'sent', 'not_sent', 'unknown') NOT NULL DEFAULT 'none',
    message_id BIGINT UNSIGNED NULL,
    delivered_at DATETIME(6) NULL,
    owner_disposition ENUM('none', 'no_further_action', 'confirmed_delivered', 'confirmed_not_delivered') NOT NULL DEFAULT 'none',
    disposition_by BIGINT UNSIGNED NULL,
    disposition_at DATETIME(6) NULL,
    closed_at DATETIME(6) NULL,
    holds_song TINYINT(1) NOT NULL DEFAULT 1,
    CONSTRAINT uq_grants_incident UNIQUE (incident_id),
    CONSTRAINT fk_grants_incident FOREIGN KEY (incident_id) REFERENCES exchange_incidents (incident_id),
    CONSTRAINT chk_grants_claim CHECK (state <> 'claimed' OR claim_token IS NOT NULL),
    CONSTRAINT chk_grants_sent CHECK (state <> 'sent' OR delivery_fact = 'sent'),
    CONSTRAINT chk_grants_holds CHECK (holds_song IN (0, 1)),
    INDEX ix_grants_open (discord_user_id, closed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Append-only audit: the application never updates or deletes these rows.
CREATE TABLE exchange_incident_events (
    event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    incident_id BIGINT UNSIGNED NOT NULL,
    action VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor BIGINT UNSIGNED NULL,
    at DATETIME(6) NOT NULL,
    detail VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    INDEX ix_events_incident (incident_id, event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
