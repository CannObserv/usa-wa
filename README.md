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
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload
```

Reachable at `https://usa-wa.exe.xyz:8001/` via the exe.dev proxy.

## Tests

```bash
# Full suite (requires TEST_DATABASE_URL)
uv run pytest

# Single file (skip the coverage gate, which measures all of packages/)
uv run pytest --no-cov packages/usa-wa-api/tests/test_health.py

# Integration tests only (excluded by default)
uv run pytest -m integration
```

`TEST_DATABASE_URL` must be a dedicated test database, distinct from `DATABASE_URL` — the
test conftest enforces this and `Base.metadata.drop_all` runs on teardown.

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

Full command reference: [`docs/COMMANDS.md`](docs/COMMANDS.md)

## Deploy

The systemd units live under [`deploy/`](deploy/) — the live API plus a sync
sidecar, a migrate oneshot, and two timer-driven oneshots.

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

```bash
sudo systemctl enable --now usa-wa-wsl-refresh.timer                 # daily 06:00 UTC
sudo systemctl enable --now usa-wa-reconcile-committee-active.timer  # weekly Sun 07:00 UTC
sudo systemctl list-timers 'usa-wa-*'                               # verify next-elapse
```
