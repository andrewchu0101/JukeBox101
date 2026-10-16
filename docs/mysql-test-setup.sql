-- Phase 1D: REVIEW AND RUN MANUALLY IN MYSQL WORKBENCH ONLY.
-- No application or test runner executes this file.
-- Stop if this database already exists; do not reuse an unreviewed database.
CREATE DATABASE jukebox101_test CHARACTER SET utf8mb4;
USE jukebox101_test;

-- Paste the reviewed bot/database/schema.sql BELOW this USE statement
-- in the SAME Workbench editor/session, then manually execute its six CREATE TABLEs.
-- Do not change or select jukebox101 for this setup.

-- Create jukebox101_test_app@127.0.0.1 manually in Workbench user management.
-- Choose a private password locally; do not paste it into repository files or chat.
-- After that account exists, manually review and execute this grant:
GRANT SELECT, INSERT, UPDATE, DELETE ON jukebox101_test.*
    TO 'jukebox101_test_app'@'127.0.0.1';

-- Do not grant schema management privileges or access to jukebox101.
-- No automatic cleanup, database deletion or account changes are provided.
