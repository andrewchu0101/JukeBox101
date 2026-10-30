-- JukeBox101 PostgreSQL control row, version 0003 (fresh, empty database only).
-- A brand-new database has no legacy catalogue and no legacy writer, so its submission snapshot is complete by
-- construction: snapshot_ready and legacy_writers_quiesced are true, and reward settlement stays disabled.
-- This migration refuses to run if any business table already holds rows; it never certifies imported data.
-- Importing older MySQL data is a separate, unexecuted plan (docs/deploy/mysql-to-postgresql-migration-plan.md).

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM users) OR EXISTS (SELECT 1 FROM songs) OR EXISTS (SELECT 1 FROM submissions)
       OR EXISTS (SELECT 1 FROM recommendations) OR EXISTS (SELECT 1 FROM credit_ledger)
       OR EXISTS (SELECT 1 FROM jb2_events) OR EXISTS (SELECT 1 FROM jb2_videos) OR EXISTS (SELECT 1 FROM jb2_versions)
       OR EXISTS (SELECT 1 FROM jb2_control) OR EXISTS (SELECT 1 FROM nickel_grants) THEN
        RAISE EXCEPTION 'fresh control bootstrap requires an empty database; imported data needs its own reviewed plan';
    END IF;
END
$$;

INSERT INTO jb2_control (singleton_id, schema_version, accepted_sequence, revision,
                         snapshot_ready, legacy_writers_quiesced, settlement_enabled)
VALUES (1, '2b2-b1', 0, 0, TRUE, TRUE, FALSE);
