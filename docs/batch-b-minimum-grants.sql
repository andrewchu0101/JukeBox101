-- REVIEW DRAFT ONLY: execute manually only after separate database-operation approval.
-- MySQL 8; dedicated existing test schema only; never run against production.
-- Replace the placeholder locally. Do not paste real passwords into chat or source files.
CREATE USER 'jukebox101_test_migrator'@'127.0.0.1' IDENTIFIED BY '<LOCAL_STRONG_PASSWORD>' ACCOUNT LOCK;
-- Stop if the account already exists; do not silently ALTER or overwrite another account.
GRANT REFERENCES, SELECT ON `jukebox101_test`.`credit_ledger` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`daily_dispatches` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_barriers` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_claims` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, INSERT, SELECT ON `jukebox101_test`.`jb2_control` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_decisions` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, REFERENCES, SELECT ON `jukebox101_test`.`jb2_events` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_evidence` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_exceptions` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_exclusions` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, INSERT, SELECT ON `jukebox101_test`.`jb2_legacy` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, INSERT, SELECT, UPDATE ON `jukebox101_test`.`jb2_migrations` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_receipts` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_reminder_batches` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_reminders` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_review_history` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, SELECT ON `jukebox101_test`.`jb2_settlements` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, REFERENCES, SELECT ON `jukebox101_test`.`jb2_versions` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT CREATE, REFERENCES, SELECT ON `jukebox101_test`.`jb2_videos` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`recommendations` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT REFERENCES, SELECT ON `jukebox101_test`.`songs` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`submissions` TO 'jukebox101_test_migrator'@'127.0.0.1';
GRANT REFERENCES, SELECT ON `jukebox101_test`.`users` TO 'jukebox101_test_migrator'@'127.0.0.1';
-- Future-table grants include CREATE as required by MySQL; no global/schema DDL grants.
-- After all grants are reviewed, a separately authorized DBA may unlock the account:
-- ALTER USER 'jukebox101_test_migrator'@'127.0.0.1' ACCOUNT UNLOCK;
-- Application section: ONLY AFTER migration has created every reviewed table.
-- Application account already exists. No CREATE USER, DDL or DELETE rights are needed.
-- Grants are additive: existing schema DML, roles, global or other table grants MUST first
-- be reviewed and removed by an independently authorized DBA. This script does NOT revoke.
-- Applying the GRANT lines alone cannot reduce existing privileges.
GRANT INSERT, SELECT ON `jukebox101_test`.`credit_ledger` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`daily_dispatches` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`jb2_barriers` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`jb2_claims` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT SELECT, UPDATE ON `jukebox101_test`.`jb2_control` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`jb2_decisions` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`jb2_events` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`jb2_evidence` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`jb2_exceptions` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`jb2_exclusions` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`jb2_legacy` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_migrations` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`jb2_receipts` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`jb2_reminder_batches` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`jb2_reminders` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`jb2_review_history` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`jb2_settlements` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`jb2_versions` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`jb2_videos` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`recommendations` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`songs` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT ON `jukebox101_test`.`submissions` TO 'jukebox101_test_app'@'127.0.0.1';
GRANT INSERT, SELECT, UPDATE ON `jukebox101_test`.`users` TO 'jukebox101_test_app'@'127.0.0.1';
-- No application modifications to jb2_migrations; no DELETE, DDL, GRANT OPTION or schema-wide writes.
