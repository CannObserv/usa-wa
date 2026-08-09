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

| Goal | Tool |
|------|------|
| Where is X defined / how does Y work / what files touch Z | `codebase_search` |
| Exact string/regex match (errors, log lines, known symbols) | `grep` / `rg` |
| Blast radius of changing/deleting a file or function | `codebase_impact` |
| What does an entry point actually do? | `codebase_flow` |
| Callers and callees of a function | `codebase_symbol` |
| Imports/dependents of a file | `grep` — **not** `codebase_graph_query` (see below) |
| DB schemas, deployment topology, runbook context | `codebase_context` / `codebase_context_search` |

**The file-dependency graph does not work on this repo.** `codebase_graph_build` resolves **3 edges
across 374 files** (81.8% of symbols unresolved), and `codebase_graph_query` on a module with 25
imports returns "No dependency information found". A rebuild does not fix it — the resolver does not
map `usa_wa_adapter_legislature.tenure_spans` onto
`packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/tenure_spans.py`, i.e. it cannot
follow a `uv` workspace `src` layout where the directory name is dashed and the module name is
underscored. So `codebase_graph_query`, `codebase_graph_circular`, `codebase_graph_stats`, and the
file-mode of `codebase_impact` return empty or misleading results here — treat empty output as
"tool broken", never as "no dependents". Derive import edges with `grep` instead, e.g.:

```bash
grep -rnE '^[[:space:]]*(from|import)[[:space:]]+usa_wa_adapter_' packages/*/src --include='*.py'
```

`codebase_search`, `codebase_symbol`, and the context tools are unaffected and remain preferred.
Filed upstream as gregoryfoster/skills#107; revisit this note when it is fixed.

Prefetch query — run via `ToolSearch` at session start:

`select:mcp__plugin_socraticode_socraticode__codebase_search,mcp__plugin_socraticode_socraticode__codebase_symbol,mcp__plugin_socraticode_socraticode__codebase_symbols,mcp__plugin_socraticode_socraticode__codebase_flow,mcp__plugin_socraticode_socraticode__codebase_impact,mcp__plugin_socraticode_socraticode__codebase_graph_query,mcp__plugin_socraticode_socraticode__codebase_status,mcp__plugin_socraticode_socraticode__codebase_context,mcp__plugin_socraticode_socraticode__codebase_context_search`

## Project Layout

`uv` workspace. Four-layer clearinghouse split — framework + domain shared across deployments; adapters + API per jurisdiction. See [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](docs/specs/2026-05-25-usa-wa-mvp-design.md).

**Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before adding an adapter, a data source, or a span/seat builder.** It is the reusable Layer-3 pattern: one adapter package per *jurisdiction+target* bundling every source that target publishes; each **source** a self-contained archive (own `Source`/`source_slug`/archive-key/transport/adapter/normalize/cohort/harvest); the **application** (spans/seats) source-agnostic, consuming a cohort interface — so a fact can draw on a new source without a rewrite (the `usa-wa-adapter-sos` filings + results sources are the worked example). Audit a source's coverage before building on it; never key a parser on an exact upstream string.

Per-package module reference — what each file is for and why it exists:

- [`docs/MODULES-FRAMEWORK.md`](docs/MODULES-FRAMEWORK.md) — Layers 1–2, the portable PM sync engine, the generated PM client
- [`docs/MODULES-COMMON.md`](docs/MODULES-COMMON.md) — Layer 2b `usa-wa-common`: WA vocabulary (calendar, seats, names, parties, ballot) and the cohort seam
- [`docs/MODULES-LEGISLATURE.md`](docs/MODULES-LEGISLATURE.md) — WSL adapter: transport, normalizers, daily refresh, cohort providers, probes
- [`docs/MODULES-LEGISLATURE-SPANS.md`](docs/MODULES-LEGISLATURE-SPANS.md) — tenure-span engine, operator succession, roster hygiene, span migrations
- [`docs/MODULES-PDC.md`](docs/MODULES-PDC.md) — PDC SODA adapter (identifier-only)
- [`docs/MODULES-SOS.md`](docs/MODULES-SOS.md) — SOS filings + results sources and the House Position seat application
- [`docs/MODULES-SYNC.md`](docs/MODULES-SYNC.md) — Layer 4: the API deployment, the PM sidecar and its producer CLIs, repo-root directories

## Infrastructure

**Single-VM setup.** Code committed to main is the deployed code.

`8001` = `8000 + 1`. The exe.dev proxy transparently forwards ports 3000–9999; the dev server is reachable at `https://usa-wa.exe.xyz:8001/`.

The full service table (13 systemd units and what each one does), the `OnFailure=` alerting chain (#49), and the owner/app/test DB role split (#22) are in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md). Two rules from there that bind on every task: migrations need the **owner** role (`DATABASE_URL_OWNER`) and everything else runs as the app role (`DATABASE_URL`); `USA_WA_ALERT_EMAIL` must be set or alerting fails closed.

## Server Lifecycle

**Port 8000 belongs to systemd.** Never start uvicorn manually on port 8000.

**Prod checkout stays on `main` (issue #87).** Every code-running unit carries `ExecStartPre=…/scripts/assert-main-checkout.sh` and refuses to start off-main, so a feature branch left checked out wedges the timers rather than deploying itself. Do feature work in a git worktree (see the `using-git-worktrees` skill). Recovery and the start-limit reasoning: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

**Units never sync the venv (issue #30).** Every entrypoint runs `uv run --frozen --no-sync`, so unit start cannot apply a dependency change a `git pull` landed in `uv.lock`. Dependency changes land only via a deliberate sync:

```bash
git pull
uv sync --locked                       # reconcile venv ⇄ uv.lock deliberately
sudo systemctl restart usa-wa-migrate  # if DB models changed (restart, not start — see note)
sudo systemctl restart usa-wa usa-wa-sync-powermap
```

Unit files are installed as root-owned **copies**, so `sudo cp deploy/<unit> /etc/systemd/system/` before `daemon-reload` — reload alone re-reads the stale copy and deploys nothing. The per-unit restart table, the `uv sync --locked` rationale, and the `verify-units.sh` pre-commit gate (#51) are in [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

**Dev server workflow.** Run on port `8001` so the live service stays up. Load env first:

```bash
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload --log-config packages/usa-wa-api/src/usa_wa_api/log_config.json
```

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

# Unit tier (#185) — no database of any kind; the fast inner loop
uv run pytest --no-cov -m 'not db and not integration'

# Run a subset of tests (skip the coverage gate, which measures all of packages/)
uv run pytest --no-cov packages/usa-wa-api/tests/test_health.py

# Run integration tests (requires PostgreSQL)
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
**Under uvicorn, `configure_logging()` is not enough** (#155 / gregoryfoster/skills#81): uvicorn ships `uvicorn`/`uvicorn.access`/`uvicorn.error` with `propagate=False` and their own plain-text handlers, which the root-only `configure_logging()` never reaches — journald then interleaves plain-text access lines with JSON app records. Every uvicorn invocation (the `usa-wa.service` `ExecStart` **and** the dev-server commands) therefore passes `--log-config packages/usa-wa-api/src/usa_wa_api/log_config.json`, a dictConfig routing all three through the shared `build_json_formatter` (`"()"` factory — no duplicated fmt string) with `ColorMessageFilter` on each logger to drop uvicorn's ANSI-duplicate `color_message` extra at the record source. Pinned by `packages/usa-wa-api/tests/test_log_config.py` (file validity, formatter single-sourcing, filter placement, and that the unit + docs still pass the flag).
JSON records carry `{timestamp, level, logger, message}` (#133; structlog's default key set). The `timestamp` uses the `+00:00` offset form, a deliberate deviation from the `Z`-suffix date convention below — the structlog migration (gregoryfoster/skills#68) owns the final format. `level`/`logger`/`timestamp`/`message` are reserved: never pass them in `extra={}`.

**Date & Time:**
- All UTC
- ISO 8601: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (timestamps), `YYYY-MM-DD` (dates)

**General:**
- No inline module imports; all at file top
- Docstrings for public modules, classes, functions
- Test structure mirrors source within each package (`packages/<name>/src/<pkg>/foo.py` → `packages/<name>/tests/test_foo.py`)
- Explicit imports only
- Small, focused functions

## Detail Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — the reusable Layer-3 pattern; read before adding an adapter, a source, or a span/seat builder
- [docs/ONTOLOGY.md](docs/ONTOLOGY.md) — the domain model: entities, lifecycle axes, spans-as-assignments, the three event shapes; read before adding a fact
- [docs/MODULES-FRAMEWORK.md](docs/MODULES-FRAMEWORK.md) — Layer 1–2 primitives, the PM sync engine, regenerating the PM client
- [docs/MODULES-COMMON.md](docs/MODULES-COMMON.md) — Layer 2b: the WA vocabulary package and the `CohortProvider` seam
- [docs/MODULES-LEGISLATURE.md](docs/MODULES-LEGISLATURE.md) — WSL adapter ingest, normalization, daily refresh
- [docs/MODULES-LEGISLATURE-SPANS.md](docs/MODULES-LEGISLATURE-SPANS.md) — tenure spans, operator succession, span migrations
- [docs/MODULES-PDC.md](docs/MODULES-PDC.md) — PDC winner cohorts and identifier links
- [docs/MODULES-SOS.md](docs/MODULES-SOS.md) — SOS filings/results sources, House Position seat builder
- [docs/MODULES-SYNC.md](docs/MODULES-SYNC.md) — API deployment, PM sidecar, producer CLIs, repo-root layout
- [docs/LWW-NOOP-GATE.md](docs/LWW-NOOP-GATE.md) — the local-newer no-op gate; read before adding a `write_enabled` producer descriptor
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — systemd units, failure alerting, DB roles, restart/lifecycle table
- [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md) — every environment variable and PM sidecar tunable, with defaults
- [docs/COMMANDS.md](docs/COMMANDS.md) — command index plus setup, tests, migrations, daily refresh
- [docs/COMMANDS-SUCCESSION.md](docs/COMMANDS-SUCCESSION.md) — operator succession, odd-year corroboration, committee lineage
- [docs/COMMANDS-SYNC.md](docs/COMMANDS-SYNC.md) — PM reconcilers, heals, validation, provenance and integrity
- [docs/COMMANDS-BACKFILL.md](docs/COMMANDS-BACKFILL.md) — historical harvests, span builders, one-shot migrations, write-free probes
- [docs/SKILLS.md](docs/SKILLS.md) — vendored agent skills: inventory, symlink layout, refresh procedure
