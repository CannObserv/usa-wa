-- scripts/grants.sql — idempotent DB role separation for usa-wa (issue #22).
--
-- Splits the single union-rights role into:
--   <owner>  — owns every table/sequence; the only role with DDL/DROP rights.
--              Used solely by `alembic upgrade head` (the migrate systemd unit).
--   <app>    — DML only (SELECT/INSERT/UPDATE/DELETE). Used by the live API,
--              the sync sidecar, the WSL refresh cron, and the on-box CLIs.
--              Cannot CREATE/ALTER/DROP, so it cannot accidentally migrate.
--
-- Re-runnable: every statement is idempotent. The migrate unit applies this
-- after each `alembic upgrade head` so a migration's new tables inherit grants
-- in the same deploy.
--
-- Run as a superuser (postgres) against the database being (re)owned. Role
-- names are psql variables; defaults target prod (usa_wa_owner / usa_wa_app):
--
--   prod:  psql -d usa_wa -v owner=usa_wa_owner -v app=usa_wa_app \
--                         -v reassign_from=usa_wa -f scripts/grants.sql
--
-- `reassign_from` is the legacy single role whose objects are handed to <owner>;
-- omit it (or set empty) once the one-time cutover has run.
--
-- NOT for the test DB: usa_wa_test's schemas don't exist until the suite creates
-- them per session, so the schema-grant steps below would error. The test DB
-- needs only its role + `ALTER DATABASE usa_wa_test OWNER TO usa_wa_test_owner`.
--
-- Passwords are deliberately NOT set here — never commit credentials. After the
-- first run, set them out-of-band on the migration host:
--     ALTER ROLE usa_wa_app   PASSWORD '...';
--     ALTER ROLE usa_wa_owner PASSWORD '...';

\if :{?owner}
\else
  \set owner usa_wa_owner
\endif
\if :{?app}
\else
  \set app usa_wa_app
\endif

-- 1. Roles (cluster-global; safe to repeat across databases). LOGIN so each DSN
--    can authenticate; NOSUPERUSER/NOCREATEDB/NOCREATEROLE caps blast radius.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'usa_wa_owner') THEN
    CREATE ROLE usa_wa_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'usa_wa_app') THEN
    CREATE ROLE usa_wa_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'usa_wa_test_owner') THEN
    CREATE ROLE usa_wa_test_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
  -- usa_wa_test_app: reserved for a future DML-only test path. The suite
  -- currently connects as usa_wa_test_owner (it owns its own schema lifecycle),
  -- so this role is created for symmetry with prod but is otherwise unused.
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'usa_wa_test_app') THEN
    CREATE ROLE usa_wa_test_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  END IF;
END
$$;

-- 2. One-time cutover: hand every object owned by the legacy role to <owner>.
--    No-op once reassign_from is unset or its role owns nothing here.
\if :{?reassign_from}
REASSIGN OWNED BY :"reassign_from" TO :"owner";
\endif

-- 3. Schema usage. Enumerate every app-facing schema Base.metadata declares.
--    ADD NEW SCHEMAS HERE when a migration introduces one. `public` is omitted
--    on purpose: it carries only alembic_version (migrate-only, owned by
--    postgres), so the app role never touches it and <owner> — which does not
--    own public — must not try to grant on it at steady state.
GRANT USAGE ON SCHEMA canonical, clearinghouse_core, sync TO :"owner";
GRANT USAGE ON SCHEMA canonical, clearinghouse_core, sync TO :"app";

-- 4. DML grants on all current tables + sequences for the app role.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA canonical TO :"app";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA clearinghouse_core TO :"app";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA sync TO :"app";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA canonical TO :"app";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA clearinghouse_core TO :"app";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA sync TO :"app";

-- 5. Default privileges: tables/sequences a FUTURE migration creates (as <owner>)
--    auto-grant DML to <app>, so no role lag between migrate and serve.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA canonical, clearinghouse_core, sync
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app";
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA canonical, clearinghouse_core, sync
  GRANT USAGE, SELECT ON SEQUENCES TO :"app";

-- 6. Write-once provenance (#54). The provenance spine is append-only by
--    contract; make it append-only by *grant* so the live app role physically
--    cannot rewrite stored history. After step 4 granted full DML to <app>:
--
--    a) Immutability — REVOKE UPDATE on all three tables. INSERT (+ SELECT)
--       remain, so adapters still append fetch events / payloads / citations,
--       but no serving role can rewrite an existing row's bytes. The integrity
--       sweep (re-hash vs content_hash) is the at-rest detector; this is the
--       at-rest preventer.
--    b) Permanence — REVOKE DELETE on fetch_events + citations only: those are
--       the durable provenance ledger, never deleted by the app. raw_payloads
--       deliberately KEEPS DELETE — it is the GC-able cache (see RawPayload
--       docstring + Source.retention_policy, #54), and the eventual retention
--       GC runs as the app role; archival payloads are protected by
--       retention_policy in the GC's WHERE clause, not by this grant.
--       CAVEAT (#78/#82): sponsors:<biennium> and committee-members-hist:<…>
--       payloads are NOT freely GC-eligible while tenure is archive-derived —
--       the span builders re-parse them offline each run, so dropping one
--       truncates or closes the membership/party span it attested. Any retention
--       GC must exclude those resource prefixes (or re-run the harvest after).
--
--    Only <owner> (migrations) can UPDATE/DELETE the ledger. Default privileges
--    (step 5) still grant full DML on FUTURE clearinghouse_core tables, so a new
--    append-only table must be added here — scripts/tests/test_grants_append_only.py
--    fails if a clearinghouse_core table isn't classified, forcing the decision.
--    Both REVOKEs are idempotent (a no-op on an already-revoked privilege), so
--    they re-apply cleanly after every migration like the rest of this file.
REVOKE UPDATE ON
  clearinghouse_core.fetch_events,
  clearinghouse_core.raw_payloads,
  clearinghouse_core.citations
  FROM :"app";
REVOKE DELETE ON
  clearinghouse_core.fetch_events,
  clearinghouse_core.citations
  FROM :"app";
