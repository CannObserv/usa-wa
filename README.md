# usa-wa

Washington State law, regulation, and policy tracking service.

## Setup

```bash
# Install Python dependencies (creates .venv, locks deps in uv.lock)
uv sync

# Install pre-commit hooks (runs ruff on commit)
uv run pre-commit install

# Load environment (production secrets + repo-local overrides)
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)
```

## Dev server

Live service runs as systemd on port `8000` — never start uvicorn manually on that port.
Use port `8001` (= API_PORT + 1) for the dev server so the live service stays up:

```bash
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload --log-config packages/usa-wa-api/src/usa_wa_api/log_config.json
```

Reachable at `https://usa-wa.exe.xyz:8001/` via the exe.dev proxy.

## HTTP API

A read-only `/api/v1` over the canonical data (#184) — persons, organizations, roles,
assignments (which *are* the tenure spans), sources, coverage and provenance chains — plus the
unversioned probes (`/health`, `/ready`, `/health/sync`) and the one mutating operator route
(`POST /sync/redrive`).

```bash
curl -s localhost:8001/api/v1/health/jobs | jq            # last run per job slug (#178)
curl -s localhost:8001/api/v1/sources/wa_sos_filings/coverage | jq   # declared vs verified (#180)
curl -s 'localhost:8001/api/v1/assignments?span_kind=chamber-house&as_of=2019-06-01' | jq
curl -s localhost:8001/openapi.json | jq '.paths | keys'
```

Full route inventory, the pagination contract and the identifier form:
[`docs/API.md`](docs/API.md).

## Tests

```bash
# Unit tier (#185) — no database at all; own coverage gate over packages/*/src/**
# (#198), so no flags needed. Add --no-cov for a faster, ungated inner loop
uv run pytest -m 'not db and not integration'

# Full suite (requires TEST_DATABASE_URL) — gates 80% of everything measured
uv run pytest

# Single file (--no-cov: neither gate measures a slice)
uv run pytest --no-cov packages/usa-wa-api/tests/test_health.py

# Integration tests only (excluded by default)
uv run pytest -m integration
```

`TEST_DATABASE_URL` must be a dedicated test database, distinct from `DATABASE_URL` — the
test conftest enforces this, and teardown drops each declared schema CASCADE (not
`Base.metadata.drop_all`, which fails on the circular bill ↔ bill_version FKs).

## Database migrations

Migrations need the owner role (DDL). In production, run the oneshot unit
(`alembic upgrade head` + `scripts/grants.sql` under `DATABASE_URL_OWNER`):
`sudo systemctl start usa-wa-migrate`. Ad-hoc `alembic` works when
`DATABASE_URL_OWNER` is set (`alembic/env.py` prefers it over `DATABASE_URL`):

```bash
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"
```

## Lint

```bash
uv run ruff check .
uv run ruff format .
```

Full command reference: [`docs/COMMANDS.md`](docs/COMMANDS.md) — it carries the
index of every operational and backfill CLI, grouped by the reference that
documents each: [succession](docs/COMMANDS-SUCCESSION.md),
[PM sync](docs/COMMANDS-SYNC.md), [backfill](docs/COMMANDS-BACKFILL.md),
[seat facts](docs/COMMANDS-SEATS.md).

Agent-facing docs (architecture, per-package module maps, deployment,
environment) are indexed under **Detail Docs** in [`AGENTS.md`](AGENTS.md).

## Deploy

The systemd units live under [`deploy/`](deploy/) — the live API plus a sync
sidecar, a migrate oneshot, and eleven timer-driven oneshots.

Production secrets live in `/etc/usa-wa/.env` (managed manually on the VM, not in
the repo) — **this file must exist before enabling any unit**, or migrate (owner
DSN) and the services (app DSN) fail to start. The unit's `ExecStartPre` writes
the current git SHA to `/run/usa-wa/build-id` and exposes it as `BUILD_ID`.

To install on a fresh host, copy all units, then enable in this order at
provision time — migrate first, run synchronously by `--now`. Boot ordering is
already enforced by the units (the API and sidecar declare
`After=usa-wa-migrate.service`, and migrate declares the reciprocal `Before=`;
the timer-driven oneshots below carry the same `After=`), so a reboot can't serve
against a not-yet-migrated schema. The `--now` here is for provision-time
synchrony: it runs migrate to completion before you enable the services in the
same session.

```bash
# Copy all units into systemd's path
sudo cp deploy/usa-wa*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload

# 1. Migrate to head (owner role; RemainAfterExit oneshot — runs once now)
sudo systemctl enable --now usa-wa-migrate

# 2. Long-running services (app role)
sudo systemctl enable --now usa-wa usa-wa-sync-powermap

# Tail logs
sudo journalctl -u usa-wa -f
```

### Scheduled units

The deploy also ships timer-driven oneshots; a fresh host must `enable` their
**timers** explicitly — they are not pulled in by `usa-wa.service`. (The units
above already landed in `/etc/systemd/system/` via the `usa-wa*` copy.)

Enable **all** of them. Four of the dailies are invariant gates whose whole job
is to exit 1 and email the operator (`OnFailure=`, #49) when the data drifts —
skip one and nothing fails, nothing alerts, and the absence looks identical to
"no drift".

```bash
# Ingest (daily)
sudo systemctl enable --now usa-wa-wsl-refresh.timer                        # daily 06:00 UTC
sudo systemctl enable --now usa-wa-pdc-refresh.timer                        # daily 06:30 UTC (#69 identifier links)
sudo systemctl enable --now usa-wa-sos-refresh.timer                        # daily 06:45 UTC (#101 House Position)

# Invariant gates (daily) — exit 1 → operator email
sudo systemctl enable --now usa-wa-senate-corroboration.timer               # daily 07:00 UTC (#123)
sudo systemctl enable --now usa-wa-house-corroboration.timer                # daily 07:05 UTC (#149)
sudo systemctl enable --now usa-wa-succession-invariants.timer              # daily 07:15 UTC (#107)
sudo systemctl enable --now usa-wa-committee-lineage-invariants.timer       # daily 07:30 UTC (#124 C4)

# Reconcile + sweep (weekly)
sudo systemctl enable --now usa-wa-reconcile-committee-active.timer         # weekly Sun 07:00 UTC
sudo systemctl enable --now usa-wa-reconcile-committee-names.timer          # weekly Sun 07:30 UTC
sudo systemctl enable --now usa-wa-reconcile-committee-meeting-names.timer  # weekly Sun 07:45 UTC (#56)
sudo systemctl enable --now usa-wa-integrity-sweep.timer                    # weekly Sun 08:00 UTC

sudo systemctl list-timers 'usa-wa-*'                                       # verify next-elapse
```

What each one does is in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) § Services.
This block is pinned against `deploy/*.timer` by
`scripts/tests/test_docs_timer_drift.py` (#167) — a new timer fails the suite
until it is listed here with its own `OnCalendar=` cadence.
