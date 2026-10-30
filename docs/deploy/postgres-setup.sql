-- JukeBox101 PostgreSQL one-time setup (run by the database administrator, NOT by the Bot or the migrator).
-- Target: PostgreSQL 18 (verified 18.6). Works with a superuser or a managed-service admin that has CREATEROLE/CREATEDB.
--
-- Usage (psql, as the administrator, connected to the maintenance database, e.g. "postgres"):
--   \i postgres-setup.sql
-- Then set each login password interactively so no password is written to a file or shell history:
--   \password jukebox101_migrator
--   \password jukebox101_app
--   \password jukebox101_readonly
-- Store the app password in the host secret file db_password.txt and the migrator password in db_migrator_password.txt
-- (see docs/deploy/v7-postgresql-deployment.md). Never give the migrator or administrator password to the Bot.

-- 1. Privilege groups (NOLOGIN). Table privileges are granted to these groups by migration 0002.
CREATE ROLE jukebox101_app_role NOLOGIN;
CREATE ROLE jukebox101_readonly_role NOLOGIN;

-- 2. Login roles. None is a superuser, none may create databases or roles.
CREATE ROLE jukebox101_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
CREATE ROLE jukebox101_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20
    IN ROLE jukebox101_app_role;
CREATE ROLE jukebox101_readonly LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5
    IN ROLE jukebox101_readonly_role;

-- 3. Dedicated database. Skip CREATE DATABASE if your provider created it; keep the REVOKE/GRANT lines.
CREATE DATABASE jukebox101 ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE jukebox101 FROM PUBLIC;
GRANT CONNECT ON DATABASE jukebox101 TO jukebox101_migrator, jukebox101_app, jukebox101_readonly;

-- 4. Schema owned by the migrator; nobody else may create objects.
\connect jukebox101
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA jukebox101 AUTHORIZATION jukebox101_migrator;
REVOKE ALL ON SCHEMA jukebox101 FROM PUBLIC;
GRANT USAGE ON SCHEMA jukebox101 TO jukebox101_app_role, jukebox101_readonly_role;

-- 5. Next step (as jukebox101_migrator, from the Bot image): python -m bot.migrate plan, then python -m bot.migrate apply --apply
