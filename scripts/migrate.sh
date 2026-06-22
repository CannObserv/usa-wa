#!/usr/bin/env bash
# Migrate + re-grant, under the DDL-owning role. Invoked by the
# usa-wa-migrate.service oneshot unit (and runnable by hand on the migrate host).
#
# Requires DATABASE_URL_OWNER in the environment (the systemd unit loads it from
# /etc/usa-wa/.env; the live API + sidecar units never carry it). Applies
# scripts/grants.sql afterward so a migration's new tables inherit app grants in
# the same deploy. The grants step runs as <owner>, so it is a no-op for role
# creation/reassignment — those are one-time, superuser-run provisioning steps.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${DATABASE_URL_OWNER:?DATABASE_URL_OWNER is required (the owner-role DSN)}"

# --frozen --no-sync: run against the installed venv as-is; never re-lock or
# sync here. Dependency changes land only via a deliberate `uv sync` in the
# deploy runbook (AGENTS.md § Server Lifecycle, issue #30).
uv run --frozen --no-sync alembic upgrade head

# psql speaks libpq, not SQLAlchemy — strip the async driver tag from the DSN.
psql "${DATABASE_URL_OWNER/+asyncpg/}" -v ON_ERROR_STOP=1 -f scripts/grants.sql
