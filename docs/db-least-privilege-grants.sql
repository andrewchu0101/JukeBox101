-- REVIEW DRAFT ONLY. Do not execute against any production database.
-- Runtime account for the Bot container. Privileges are derived from the executed SQL in bot/ (see docs/db-least-privilege.md).
-- Replace every <PLACEHOLDER> locally in a reviewed copy. Never paste real passwords into chat, source files or this document.
-- Execute only in a disposable, explicitly approved isolated schema; the Owner reviews the final statements first.

-- Create the runtime account only if it does not already exist (fail rather than overwrite).
CREATE USER '<APP_USER>'@'<APP_HOST>' IDENTIFIED BY '<LOCAL_STRONG_PASSWORD>' ACCOUNT LOCK;

-- Business tables: read, insert and update only. No DELETE, DDL, GRANT or administrative privileges.
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`users` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`credit_ledger` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`songs` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`recommendations` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`submissions` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`jb2_control` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`jb2_videos` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`jb2_versions` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`jb2_evidence` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`jb2_claims` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`jb2_barriers` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`jb2_exclusions` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`jb2_legacy` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`jb2_events` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`jb2_settlements` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`jb2_receipts` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT, UPDATE ON `<SCHEMA>`.`jb2_reminders` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`jb2_reminder_batches` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`jb2_review_history` TO '<APP_USER>'@'<APP_HOST>';

-- Nickel grant tables (migration 004 and 005). Writes are limited to the claim transaction.
GRANT SELECT, INSERT ON `<SCHEMA>`.`nickel_grants` TO '<APP_USER>'@'<APP_HOST>';
GRANT SELECT, INSERT ON `<SCHEMA>`.`nickel_claim_requests` TO '<APP_USER>'@'<APP_HOST>';

-- Intentionally NOT granted: jb2_migrations (migration path only), any DELETE, any DDL, GRANT, SUPER, FILE, PROCESS.
-- Compensation tables (exchange_incidents, exchange_incident_events, exchange_redelivery_grants) are NOT granted:
-- they exist only in the candidate docs/compensation-candidate-004.sql, which is not an approved migration.
-- Add grants for them only after that candidate is approved and applied.
-- Verify after applying: SHOW GRANTS FOR '<APP_USER>'@'<APP_HOST>'; then run the negative checks in docs/db-least-privilege.md section 5.
