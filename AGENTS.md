# usa-wa — Agent Guidelines

Be terse. Prefer fragments over full sentences. Skip filler and preamble. Sacrifice grammar for density. Lead with the answer or action.

## Project Overview

Washington State law, regulation, and policy tracking service.

## Development Methodology

TDD required. Red → Green → Refactor. No production code without a failing test first.

## Environment & Tooling

Python ≥3.12, uv, pytest, ruff.

## Code Exploration Policy

SocratiCode is the preferred semantic-search tool for this repo (once indexed; the index lives in `.socraticodecontextartifacts.json` once `codebase_index` has run). Its MCP tools are **deferred** — schemas load only after a `ToolSearch` prefetch.

**Negative rule.** For broad semantic questions ("where is X", "how does Y work", "what depends on Z"), use SocratiCode MCP tools first. Reach for `grep`/`ripgrep` only on exact strings (error messages, log lines, known symbols). Reserve the Explore subagent for path-pattern walks (e.g. "all `*.py` under `packages/usa-wa-api/src/usa_wa_api/api/`"), not semantic search.

**The file-dependency graph is broken here.** Empty output from `codebase_graph_query` /
`_circular` / `_stats` or the file-mode of `codebase_impact` means "tool broken", never "no
dependents" — derive import edges with `grep`. The goal→tool table, the measurements behind that
finding, and the source of the session-start `ToolSearch` prefetch query:
[`docs/CODE-EXPLORATION.md`](docs/CODE-EXPLORATION.md).

## Project Layout

`uv` workspace. Four-layer clearinghouse split — framework + domain shared across deployments; adapters + API per jurisdiction. See [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](docs/specs/2026-05-25-usa-wa-mvp-design.md).

**Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before adding an adapter, a data source, or a span/seat builder** — the reusable Layer-3 pattern, in full, with the worked example. Two rules bind whatever you are building: audit a source's coverage before building on it, and never key a parser on an exact upstream string. Inside a package (#183), **`harvest.py` = Phase A, `build.py` = Phase B**.

**Six layers since #189 (AR-14), enforced by `import-linter`** — `uv run lint-imports`, wired into the pre-commit gate beside ruff, contracts + rationale in the root `pyproject.toml`:

| Layer | Package(s) | Rule |
|---|---|---|
| 1 framework | `clearinghouse-core` | jurisdiction-agnostic primitives |
| 2 domain | `clearinghouse-domain-legislative` | the legislative model **+ the term calendar, the span engine and the `CohortProvider` Protocols** |
| 2b vocabulary | `usa-wa-common` | WA facts, source-free — **may not import an adapter** |
| 3 adapters | `usa-wa-adapter-*` | sourcing only, one per jurisdiction+target — **no adapter may import a peer adapter** |
| 3b facts | `usa-wa-facts-*` | applications composing cohorts across adapters — **never an adapter's `transport`** |
| 4 deployment | `usa-wa-api`, `usa-wa-sync-powermap` | serve + sync — **never an adapter's `transport`** |

Per-package module reference — what each file is for and why it exists:

- [`docs/MODULES-FRAMEWORK.md`](docs/MODULES-FRAMEWORK.md) — Layers 1–2: the framework + domain primitives
- [`docs/MODULES-SYNC-ENGINE.md`](docs/MODULES-SYNC-ENGINE.md) — the portable PM sync engine + the generated PM client
- [`docs/MODULES-COMMON.md`](docs/MODULES-COMMON.md) — Layer 2b `usa-wa-common`: WA vocabulary (calendar, seats, names, parties, ballot) and the cohort seam
- [`docs/MODULES-LEGISLATURE.md`](docs/MODULES-LEGISLATURE.md) — WSL adapter: transport, normalizers, daily refresh, cohort providers, probes
- [`docs/MODULES-LEGISLATURE-ROSTER.md`](docs/MODULES-LEGISLATURE-ROSTER.md) — the roster-PDF source: parser, audit oracle, succession → resolve → backfill
- [`docs/MODULES-LEGISLATURE-SPANS.md`](docs/MODULES-LEGISLATURE-SPANS.md) — tenure-span engine, operator succession, roster hygiene, span migrations
- [`docs/MODULES-PDC.md`](docs/MODULES-PDC.md) — PDC SODA adapter (identifier-only)
- [`docs/MODULES-SOS.md`](docs/MODULES-SOS.md) — SOS filings + results sources
- [`docs/MODULES-FACTS-SEATS.md`](docs/MODULES-FACTS-SEATS.md) — Layer 3b `usa-wa-facts-seats`: the composition layer (House Position, Senate corroboration, PDC spans)
- [`docs/MODULES-SYNC.md`](docs/MODULES-SYNC.md) — Layer 4: the API deployment, the PM sidecar daemon, repo-root directories
- [`docs/MODULES-SYNC-PRODUCERS.md`](docs/MODULES-SYNC-PRODUCERS.md) — the one-shot PM producer CLIs: reconcilers, heals, validation, retraction

## Infrastructure

**Single-VM setup.** Code committed to main is the deployed code.

`8001` = `8000 + 1`. The exe.dev proxy transparently forwards ports 3000–9999; the dev server is reachable at `https://usa-wa.exe.xyz:8001/`.

The full service table (every systemd unit and what each one does), the `OnFailure=` alerting chain (#49), and the owner/app/test DB role split (#22) are in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). Two rules from there that bind on every task: migrations need the **owner** role (`DATABASE_URL_OWNER`) and everything else runs as the app role (`DATABASE_URL`); `USA_WA_ALERT_EMAIL` must be set or alerting fails closed.

## Server Lifecycle

**Port 8000 belongs to systemd.** Never start uvicorn manually on port 8000.

**Prod checkout stays on `main` (issue #87).** Every code-running unit carries `ExecStartPre=…/scripts/assert-main-checkout.sh` and refuses to start off-main, so a feature branch left checked out wedges the timers rather than deploying itself. Do feature work in a git worktree (see the `using-git-worktrees` skill). Recovery and the start-limit reasoning: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

Run `uv sync --locked` in a new worktree: `.skills/worktree_venv=none` links no venv on purpose ([`docs/SKILLS.md`](docs/SKILLS.md#worktree-venv-isolation)).

**Units never sync the venv (issue #30).** Every entrypoint runs `uv run --frozen --no-sync`, so unit start cannot apply a dependency change a `git pull` landed in `uv.lock`. Dependency changes land only via a deliberate sync:

```bash
git pull
uv sync --locked                       # reconcile venv ⇄ uv.lock deliberately
sudo systemctl restart usa-wa-migrate  # if DB models changed (restart, not start — see note)
sudo systemctl restart usa-wa usa-wa-sync-powermap
```

Unit files are installed as root-owned **copies**, so `sudo cp deploy/<unit> /etc/systemd/system/` before `daemon-reload` — reload alone re-reads the stale copy and deploys nothing. The per-unit restart table, the `uv sync --locked` rationale, and the `verify-units.sh` pre-commit gate (#51) are in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

**Dev server workflow.** Run on port `8001` so the live service stays up, loading the env
first — both commands are in § Common Commands below.

**After finishing work.** Always restart the systemd service to pick up changes merged to main:

```bash
sudo systemctl restart usa-wa
```

## Environment Variables

Two env files, loaded in order (later values override):

1. **`/etc/usa-wa/.env`** — production secrets (`DATABASE_URL`, etc.). Survives repo resets and worktree switches. Managed manually on the VM.
2. **`.env`** (repo root, git-ignored) — dev/agent secrets (`GH_TOKEN`, `TEST_DATABASE_URL`). Never commit.

The systemd service loads both automatically. For shell commands:

```bash
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)
```

Every variable the deployment reads — including the PM sidecar tunables — is documented in [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md).

## Common Commands

```bash
# Install dependencies
uv sync

# Load environment (required before running server, migrations, or gh)
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)

# Run tests
uv run pytest

# Concurrent db-marked runs serialize on a Postgres advisory lock, then fail
# loudly (#208) — see docs/COMMANDS.md

# Unit tier (#185) — no database at all; own coverage gate (#198), so no flags
# needed. Add --no-cov for a faster (~11s vs ~27s), ungated inner loop
uv run pytest -m 'not db and not integration'

# A subset — --no-cov: neither gate measures a slice
uv run pytest --no-cov packages/usa-wa-api/tests/test_health.py

# Run integration tests (requires PostgreSQL; hits live services). Exempt from the
# coverage floor (#216) — green exits 0, red exits non-zero, no flags needed
uv run pytest -m integration

# Run linter
uv run ruff check .

# Database migrations (need the owner role — see docs/DEPLOYMENT.md § DB role topology)
# prod: sudo systemctl restart usa-wa-migrate (restart, not start — RemainAfterExit
#       oneshot no-ops on start once already active); ad-hoc alembic needs DATABASE_URL_OWNER
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"

# FastAPI dev server
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload --log-config packages/usa-wa-api/src/usa_wa_api/log_config.json
```

Everyday commands only. Every operational and backfill CLI is indexed in [`docs/COMMANDS.md`](docs/COMMANDS.md), which links the grouped references (succession, PM sync, backfill). Prod runs the daily/weekly ones on systemd timers (see § Server Lifecycle); pair a backfill with `USA_WA_BIENNIUM` to target a non-current biennium.

## Agent Skills

Skills in `skills/` (agentskills.io) and `.claude/skills/` (Claude Code). Reference: `docs/SKILLS.md`

## Conventions

**Commit Messages:**
```
#<number> [type]: <description>      # with issue
[type]: <description>                # without issue
```
Types: feat, fix, refactor, docs, test, chore

**Logging:**
```python
from clearinghouse_core.logging import get_logger
logger = get_logger(__name__)
```
Entry points only: `configure_logging()` is called once inside the FastAPI `lifespan`. Never in library modules.
**Under uvicorn, `configure_logging()` alone is not enough** (#155): every uvicorn invocation — the `usa-wa.service` `ExecStart` **and** the dev-server commands — must pass `--log-config packages/usa-wa-api/src/usa_wa_api/log_config.json`, or journald interleaves plain-text access lines with JSON app records. Why, and what pins it: [`docs/LOGGING.md`](docs/LOGGING.md).
JSON records carry `{timestamp, level, logger, message}` (#133). `level`/`logger`/`timestamp`/`message` are reserved: never pass them in `extra={}`.

**Date & Time:**
- All UTC
- ISO 8601: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (timestamps), `YYYY-MM-DD` (dates)
- One deliberate exception: a log record's `timestamp` uses the `+00:00` offset form, not the `Z` suffix ([`docs/LOGGING.md`](docs/LOGGING.md))

**General:**
- No inline module imports; all at file top
- Docstrings for public modules, classes, functions
- Test structure mirrors source within each package (`packages/<name>/src/<pkg>/foo.py` → `packages/<name>/tests/test_foo.py`)
- Explicit imports only
- Small, focused functions

## Detail Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the reusable Layer-3 pattern; read before adding an adapter, a source, or a span/seat builder
- [docs/ONTOLOGY.md](docs/ONTOLOGY.md) — the domain model: entities, lifecycle axes, spans-as-assignments, the three event shapes; read before adding a fact
- the `docs/MODULES-*.md` per-package references are listed under § Project Layout above — one entry each, not repeated here
- [docs/CODE-EXPLORATION.md](docs/CODE-EXPLORATION.md) — goal→tool table, the broken file-dependency graph, the `ToolSearch` prefetch
- [docs/LOGGING.md](docs/LOGGING.md) — the JSON record shape and why every uvicorn invocation passes `--log-config`
- [docs/API.md](docs/API.md) — the read-only `/api/v1` surface: route inventory, pagination, and the response contracts
- [docs/LWW-NOOP-GATE.md](docs/LWW-NOOP-GATE.md) — the local-newer no-op gate; read before adding a `write_enabled` producer descriptor
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — systemd units, failure alerting, DB roles, restart/lifecycle table
- [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) — every environment variable and PM sidecar tunable, with defaults
- [docs/COMMANDS.md](docs/COMMANDS.md) — command index plus setup, tests, migrations, daily refresh
- [docs/COMMANDS-SUCCESSION.md](docs/COMMANDS-SUCCESSION.md) — operator succession, odd-year corroboration, committee lineage
- [docs/COMMANDS-SYNC.md](docs/COMMANDS-SYNC.md) — PM reconcilers, heals, validation, provenance and integrity
- [docs/COMMANDS-BACKFILL.md](docs/COMMANDS-BACKFILL.md) — historical harvests, span builders, one-shot migrations, write-free probes
- [docs/COMMANDS-SEATS.md](docs/COMMANDS-SEATS.md) — the seat-fact backfills: PDC identifier links (#79), WSL+SOS House Position (#101)
- [docs/SKILLS.md](docs/SKILLS.md) — vendored agent skills: inventory, symlink layout, refresh procedure
