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

The systemd unit lives at [`deploy/usa-wa.service`](deploy/usa-wa.service). To install on a fresh host:

```bash
# Copy into systemd's path
sudo cp deploy/usa-wa.service /etc/systemd/system/usa-wa.service
sudo systemctl daemon-reload
sudo systemctl enable --now usa-wa

# Tail logs
sudo journalctl -u usa-wa -f
```

Production secrets live in `/etc/usa-wa/.env` (managed manually on the VM, not in the repo). The unit's `ExecStartPre` writes the current git SHA to `/run/usa-wa/build-id` and exposes it as `BUILD_ID`.
