-- REVIEW ONLY. One-time DBA operation, not executed by tests or Codex.
-- The existing app, migrator, schema and migration registry remain unchanged.
-- Stop if this exact harness account already exists; do not silently replace its credentials/grants.
-- EXECUTION CONTRACT: the authorized executor must inspect every result below and STOP
-- before CREATE USER unless all conditions match. SELECT alone does not abort a SQL batch.
-- Connect explicitly by TCP to 127.0.0.1:3306; select only jukebox101_test as the session database.
SELECT DATABASE() AS target_database, @@port AS server_port, @@server_uuid AS server_uuid,
       VERSION() AS mysql_version, @@mandatory_roles AS mandatory_roles;
-- Require target_database=jukebox101_test, server_port=3306, MySQL 8.0.22+;
-- retain the approved nonsecret UUID as TEST_DB_SERVER_UUID; require no mandatory roles.
SELECT TABLE_NAME, TABLE_TYPE, ENGINE FROM information_schema.TABLES
WHERE TABLE_SCHEMA='jukebox101_test' ORDER BY TABLE_NAME;
-- Require exactly the reviewed 23 InnoDB BASE TABLE names, not merely a count of 23.
SELECT TRIGGER_NAME, EVENT_OBJECT_TABLE FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA='jukebox101_test';
-- Must be empty from a DBA identity with complete trigger visibility.
SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, ORDINAL_POSITION,
       REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA='jukebox101_test' AND REFERENCED_TABLE_SCHEMA IS NOT NULL;
-- Require every FK target schema to be jukebox101_test and the reviewed target table/columns.
SELECT USER, HOST FROM mysql.user WHERE USER='jukebox101_test_harness';
-- Must be empty across all hosts. No authentication fields are selected.
-- The DBA must have complete metadata visibility; no credentials/secret fields are queried.
-- CREATE USER plus GRANT statements are not one atomic transaction. Stop at the first error.
CREATE USER 'jukebox101_test_harness'@'127.0.0.1' IDENTIFIED BY '<REPLACE_LOCALLY_WITH_STRONG_PASSWORD>' ACCOUNT LOCK;
-- Execute table grants only after confirming the accepted 23-table isolated schema.
-- No schema/global privileges, GRANT OPTION, DDL, INSERT, or registry modifications.
GRANT DELETE, SELECT ON `jukebox101_test`.`credit_ledger` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`daily_dispatches` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_barriers` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_claims` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_control` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_decisions` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_events` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_evidence` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_exceptions` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_exclusions` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_legacy` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`jb2_migrations` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_receipts` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_reminder_batches` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_reminders` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_review_history` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_settlements` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_versions` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`jb2_videos` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`recommendations` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`songs` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT SELECT ON `jukebox101_test`.`submissions` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT DELETE, SELECT ON `jukebox101_test`.`users` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT UPDATE (`accepted_sequence`, `revision`, `snapshot_ready`, `legacy_writers_quiesced`, `settlement_enabled`) ON `jukebox101_test`.`jb2_control` TO 'jukebox101_test_harness'@'127.0.0.1';
GRANT UPDATE (`next_due_at`) ON `jukebox101_test`.`jb2_reminders` TO 'jukebox101_test_harness'@'127.0.0.1';
-- MySQL grants table-wide DELETE. Only the reviewed harness code restricts it to verified primary keys.
-- DBA must verify there are no hidden objects/triggers, other grants or assignable roles.
-- Never change app/migrator privileges or unlock the migrator for these tests.
-- After success: verify the new account metadata, no role assignments, global/dynamic,
-- schema, routine, proxy or excess column grants. Do not print authentication properties.
-- The harness rechecks its own SHOW GRANTS and APPLICABLE_ROLES without logging raw grant text.
-- Keep the DBA connection out of all business tests; never unlock the migrator.
-- POST-GRANT gate: the authorized executor must verify all results before unlocking.
SELECT USER, HOST, account_locked FROM mysql.user
WHERE USER='jukebox101_test_harness';
-- Require exactly the new @127.0.0.1 account with account_locked=Y.
SELECT GRANTEE, PRIVILEGE_TYPE, IS_GRANTABLE FROM information_schema.USER_PRIVILEGES
WHERE GRANTEE='''jukebox101_test_harness''@''127.0.0.1''';
SELECT GRANTEE, TABLE_SCHEMA, PRIVILEGE_TYPE, IS_GRANTABLE FROM information_schema.SCHEMA_PRIVILEGES
WHERE GRANTEE='''jukebox101_test_harness''@''127.0.0.1''';
SELECT TABLE_SCHEMA, TABLE_NAME, PRIVILEGE_TYPE, IS_GRANTABLE FROM information_schema.TABLE_PRIVILEGES
WHERE GRANTEE='''jukebox101_test_harness''@''127.0.0.1''';
SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, PRIVILEGE_TYPE, IS_GRANTABLE FROM information_schema.COLUMN_PRIVILEGES
WHERE GRANTEE='''jukebox101_test_harness''@''127.0.0.1''';
SELECT PRIV, WITH_GRANT_OPTION FROM mysql.global_grants
WHERE USER='jukebox101_test_harness' AND HOST='127.0.0.1';
SELECT FROM_USER, FROM_HOST, WITH_ADMIN_OPTION FROM mysql.role_edges
WHERE TO_USER='jukebox101_test_harness' AND TO_HOST='127.0.0.1';
SELECT Db, Routine_name, Routine_type, Proc_priv FROM mysql.procs_priv
WHERE User='jukebox101_test_harness' AND Host='127.0.0.1';
SELECT Proxied_user, Proxied_host, With_grant FROM mysql.proxies_priv
WHERE User='jukebox101_test_harness' AND Host='127.0.0.1';
SHOW GRANTS FOR 'jukebox101_test_harness'@'127.0.0.1';
-- Require no global/schema/dynamic/routine/proxy/role grants or grant options;
-- table/column grants must exactly equal the reviewed Python harness requirements.
-- Only after every gate passes, unlock this NEW harness account, never the migrator.
ALTER USER 'jukebox101_test_harness'@'127.0.0.1' ACCOUNT UNLOCK;
-- Reconnect as harness, verify CURRENT_USER, UUID, own grants and no applicable roles.
-- On any setup/verification failure, stop; leave/re-lock ONLY this harness account.
-- Never silently DROP/RECREATE or widen grants to make verification pass.
