-- JukeBox101 PostgreSQL privileges, version 0002.
-- Grants table privileges to two NOLOGIN group roles created once by the database administrator
-- (see docs/deploy/postgres-setup.sql). Login roles become members of these groups; nothing here grants LOGIN.
-- Least privilege: the Bot never deletes, truncates, references-creates or changes DDL. UPDATE is granted only where
-- the application updates rows or takes a row lock (SELECT ... FOR UPDATE requires UPDATE in PostgreSQL).
-- If a group role is missing this migration fails, so the schema cannot be marked applied without the role model.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jukebox101_app_role')
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jukebox101_readonly_role') THEN
        RAISE EXCEPTION 'group roles jukebox101_app_role and jukebox101_readonly_role must exist (docs/deploy/postgres-setup.sql)';
    END IF;
END
$$;

GRANT USAGE ON SCHEMA jukebox101 TO jukebox101_app_role, jukebox101_readonly_role;

-- Application role: read and append everywhere it works; update only where it changes or locks rows.
GRANT SELECT, INSERT ON
    users, songs, submissions, recommendations, credit_ledger,
    jb2_control, jb2_versions, jb2_videos, jb2_legacy, jb2_events, jb2_receipts, jb2_claims, jb2_settlements,
    jb2_review_history, jb2_evidence, jb2_decisions, jb2_exclusions, jb2_barriers, jb2_exceptions,
    jb2_reminders, jb2_reminder_batches, nickel_grants, nickel_claim_requests
    TO jukebox101_app_role;
GRANT UPDATE ON
    users, songs, recommendations, jb2_control, jb2_versions, jb2_videos, jb2_legacy, jb2_events, jb2_reminders
    TO jukebox101_app_role;

-- Read-only maintenance role: every table, no writes.
GRANT SELECT ON ALL TABLES IN SCHEMA jukebox101 TO jukebox101_readonly_role;
