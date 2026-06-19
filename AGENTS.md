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
| Imports/dependents of a file | `codebase_graph_query` |
| DB schemas, deployment topology, runbook context | `codebase_context` / `codebase_context_search` |

Prefetch query — run via `ToolSearch` at session start:

`select:mcp__plugin_socraticode_socraticode__codebase_search,mcp__plugin_socraticode_socraticode__codebase_symbol,mcp__plugin_socraticode_socraticode__codebase_symbols,mcp__plugin_socraticode_socraticode__codebase_flow,mcp__plugin_socraticode_socraticode__codebase_impact,mcp__plugin_socraticode_socraticode__codebase_graph_query,mcp__plugin_socraticode_socraticode__codebase_status,mcp__plugin_socraticode_socraticode__codebase_context,mcp__plugin_socraticode_socraticode__codebase_context_search`

## Project Layout

`uv` workspace. Four-layer clearinghouse split — framework + domain shared across deployments; adapters + API per jurisdiction. See [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](docs/specs/2026-05-25-usa-wa-mvp-design.md).

```
packages/
  clearinghouse-core/                 — Layer 1: framework primitives (jurisdiction-agnostic)
    src/clearinghouse_core/
      models.py       — Declarative Base, TimestampMixin (side-effect-imports jurisdictions + provenance for Base.metadata)
      jurisdictions.py — Jurisdiction cache mirror (4 tables: types/relationship_types lookups, jurisdictions, jurisdiction_relationships) — local copy of Power Map's Jurisdiction extension
      provenance.py   — Source, FetchEvent, RawPayload, Citation, Note, DocumentIdentifier (every canonical fact traces back to these)
      adapter.py      — BaseAdapter contract + FetchedPayload / NormalizedBatch / ResourceRef
      runner.py       — AdapterRunner: cache-or-fetch decision, idempotent upsert, provenance writing
      db/             — ULID SQLAlchemy column type (see db/ulid.md for rationale)
      database.py     — Async engine + session factory
      config.py       — Settings / env access (pydantic-settings)
      logging.py      — configure_logging() + get_logger()
  clearinghouse-domain-legislative/   — Layer 2: legislative-government model (state/federal)
    src/clearinghouse_domain_legislative/
                      — Bill, Legislator, BillAction, StatuteSection, etc. (skeletoned step 7)
  clearinghouse-sync-powermap/        — Layer 1-adjacent: portable Power Map sync engine (sibling-reusable)
    src/clearinghouse_sync_powermap/
      descriptors.py  — EntityDescriptor contract (per-entity sync behaviour; zero usa-wa imports)
      engine.py       — SyncEngine: changes-feed + reconcile reads, LWW, outbox worker, backoff
      client.py       — PowerMapClient Protocol + value types (ObservationResult, ChangePage…)
      models.py       — sync-schema OutboxEntry + SyncState (durable delivery ledger + feed cursor)
      testing.py      — shipped test doubles (FakeEntity/Descriptor/Client) for this + sibling tests
      pmclient.py     — GeneratedPowerMapClient: adapts the generated SDK to the PowerMapClient Protocol
  powermap-client/                    — GENERATED OpenAPI client for Power Map (do not hand-edit)
                      — openapi-python-client output; excluded from ruff/coverage/pre-commit.
                        Regenerate when PM's API changes (see "Regenerating the PM client" below).
  usa-wa-adapter-legislature/         — Layer 3: WA Legislature SOAP source mapping
    src/usa_wa_adapter_legislature/
      adapter.py      — WALegislatureAdapter(BaseAdapter): discover/fetch_one/normalize for the committees:<biennium> resource
      synthesis.py    — pure functions emitting canonical-row dicts for anchors WSL doesn't expose (legislature/chamber/biennium/regular)
      bootstrap.py    — bootstrap_synthetic_anchors: idempotent ON CONFLICT DO NOTHING upserts of the 6 anchor rows; returns BootstrapAnchors
      transport.py    — WSLClient: per-service zeep wrapper with lazy WSDL load; SOAP calls dispatched via asyncio.to_thread
      normalize/      — per-resource normalizers (committees.py: WSL Committee → canonical Organization; Agency resolves the parent — House/Senate → chamber, Joint → legislature)
      refresh.py      — `python -m usa_wa_adapter_legislature.refresh` CLI entrypoint; biennium-from-date with USA_WA_BIENNIUM override
  usa-wa-api/                         — Layer 4: WA deployment (FastAPI + MCP + REST)
    src/usa_wa_api/api/
      main.py         — App factory, lifespan, router registration
      deps.py         — FastAPI dependencies (DB session, auth)
    tests/            — API tests; conftest defines savepointed db_session + AsyncClient
  usa-wa-sync-powermap/               — Layer 4: PM sync deployment binding + sidecar daemon
    src/usa_wa_sync_powermap/
      descriptors/    — concrete EntityDescriptors (jurisdiction, organization, role, person, assignment) — full identity cluster + PM-first match cascade + enrich-on-match; `events.py` is the entity-event sub-resource read-mirror (person/org `fetch_record` pulls `/{id}/events`, `upsert_from_pm` mirrors via `sync_entity_events`)
      registry.py     — build_descriptors() — the entity set the sidecar syncs
      sidecar.py      — Sidecar: per-cycle tick (feed → reconcile → sweep → drain) + isolated run loop
      config.py       — SidecarSettings (POWERMAP_BASE_URL, POWERMAP_API_KEY)
      __main__.py     — daemon entrypoint (python -m usa_wa_sync_powermap)
alembic/              — single alembic root; env.py imports clearinghouse_core.models.Base
docs/specs/           — Architecture specs (source of truth for design decisions)
docs/plans/           — Per-phase implementation plans
docs/research/        — Discovery outputs (Archiver/Watcher contracts, multi-state IA delta)
docs/                 — Reference docs (COMMANDS, SKILLS)
deploy/               — Systemd unit + deployment config
```

## Infrastructure

**Single-VM setup.** Code committed to main is the deployed code.

| Service | Framework | Port | Managed by |
|---|---|---|---|
| API (live) | FastAPI | 8000 | `systemctl` (`usa-wa.service`) |
| PM sync sidecar | asyncio daemon | — | `systemctl` (`usa-wa-sync-powermap.service`) |
| API (dev) | FastAPI | 8001 | manual uvicorn |

`8001` = `8000 + 1`. The exe.dev proxy transparently forwards ports 3000–9999; the dev server is reachable at `https://usa-wa.exe.xyz:8001/`.

### DB role topology (defense-in-depth, issue #22)

DDL and DML rights are split across roles so a misconfigured DSN can't migrate/drop the live DB:

| Role | Rights | Used by |
|---|---|---|
| `usa_wa_owner` | owns all tables/sequences; CREATE/ALTER/DROP | `alembic upgrade head` only — the `usa-wa-migrate.service` oneshot |
| `usa_wa_app` | SELECT/INSERT/UPDATE/DELETE only (no DDL) | live API, sync sidecar, WSL refresh cron, on-box CLIs |
| `usa_wa_test_owner` | owns the **separate** `usa_wa_test` database; DDL | `TEST_DATABASE_URL` — the suite owns its own schema lifecycle (`create_all`/drop per session) |

- `DATABASE_URL` (app role) serves; `DATABASE_URL_OWNER` (owner role, migrate host only) migrates. `alembic/env.py` prefers `DATABASE_URL_OWNER` when set, else `DATABASE_URL`.
- [`scripts/grants.sql`](scripts/grants.sql) is the version-controlled source of truth for grants — idempotent, re-applied after every migration by [`scripts/migrate.sh`](scripts/migrate.sh). `ALTER DEFAULT PRIVILEGES` means new tables auto-grant DML to the app role. **Add new schemas to it** when a migration introduces one.
- Provision prod once as superuser: `psql -d usa_wa -v reassign_from=usa_wa -f scripts/grants.sql` (then per-role `ALTER ROLE … PASSWORD` out-of-band; passwords are never committed).
- The **test DB** needs only its role + ownership — do **not** run `grants.sql` against it (its schemas don't exist until the suite creates them, so the schema-grant steps would error). Provision with: `psql -c "CREATE ROLE usa_wa_test_owner LOGIN PASSWORD '…'"` then `ALTER DATABASE usa_wa_test OWNER TO usa_wa_test_owner`.
- Both the API lifespan and the sidecar log a startup fingerprint (`current_user` + `current_database`) — role/DB confusion shows up in the first `journalctl` line.

## Server Lifecycle

**Port 8000 belongs to systemd.** Never start uvicorn manually on port 8000.

| Situation | Action |
|---|---|
| Code committed to main | `sudo systemctl restart usa-wa` |
| Testing a worktree/branch | `uv run uvicorn ... --port 8001 --reload` |
| Debugging the live service | `sudo journalctl -u usa-wa -f` |
| After editing `deploy/usa-wa.service` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa` |
| After DB model changes | `sudo systemctl start usa-wa-migrate` (runs alembic + grants under the owner role), then restart usa-wa |

**Dev server workflow.** Run on port `8001` so the live service stays up. Load env first:

```bash
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload
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

Currently defined:
- `GH_TOKEN` — GitHub personal access token (used by `gh` CLI)
- `DATABASE_URL` — PostgreSQL connection string (app role `usa_wa_app` — DML only)
- `DATABASE_URL_OWNER` — owner-role DSN for migrations (migrate host only; `usa-wa-migrate.service` + `scripts/migrate.sh`). `alembic/env.py` prefers it over `DATABASE_URL`. Absent from the live API/sidecar units.
- `TEST_DATABASE_URL` — PostgreSQL connection string for the test database (test role; database name must end in `_test`)
- `BUILD_ID` — git SHA stamped by the systemd unit's `ExecStartPre`; defaults to `"dev"` outside systemd
- `USA_WA_OPERATOR_TOKEN` — shared secret gating the mutating operator endpoint `POST /sync/redrive` (re-drives dead-lettered `UNAVAILABLE` outbox entries). **Fail-closed:** if unset, the endpoint is locked for everyone, so it must be set in `/etc/usa-wa/.env` before the re-drive route can be used. The on-box CLI (`python -m usa_wa_api.cli.redrive`) needs no token — shell access is the trust boundary.
- `USA_WA_BIENNIUM` — optional override for the auto-computed WA biennium label (e.g. `2025-26`) used by the WSL refresh. Without it, `refresh.py` derives the biennium from the current UTC date (WA bienniums start on odd years). Useful for backfills and early-year edge cases.

PM sidecar tunables (`SidecarSettings`, env-overridable): `OUTBOX_COMMIT_CHUNK_SIZE` (delivered entries per DB commit during a drain; default 1 = per-entry) and `POWERMAP_SEARCH_MATCH_CAP` (max candidate window the org/person name-match cascade pages; default unset = per-entity default).

## Common Commands

```bash
# Install dependencies
uv sync

# Load environment (required before running server, migrations, or gh)
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)

# Run tests
uv run pytest

# Run a subset of tests (skip the coverage gate, which measures all of packages/)
uv run pytest --no-cov packages/usa-wa-api/tests/test_health.py

# Run integration tests (requires PostgreSQL)
uv run pytest -m integration

# Run linter
uv run ruff check .

# Database migrations
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"

# FastAPI dev server
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload

# WSL refresh (cron-style; one-shot pull from CommitteeService.GetActiveCommittees)
python -m usa_wa_adapter_legislature.refresh
```

Full reference: `docs/COMMANDS.md`

### Regenerating the PM client

`packages/powermap-client/` is generated from Power Map's live OpenAPI; never hand-edit it. To refresh after PM ships API changes:

```bash
cd /tmp && rm -rf pmgen && mkdir pmgen && cd pmgen
curl -fsS https://power-map.exe.xyz/openapi.json -o pm-openapi.json
printf 'package_name_override: powermap_client\nproject_name_override: powermap-client\n' > cfg.yml
uvx openapi-python-client generate --path pm-openapi.json --config cfg.yml --meta uv
# review the diff, then replace the vendored copy:
rm -rf /home/exedev/usa-wa/packages/powermap-client
cp -r powermap-client /home/exedev/usa-wa/packages/powermap-client
```

Then `uv sync` and re-run the `GeneratedPowerMapClient` wrapper tests — the wrapper's path/model dispatch (`pmclient.py`) is what breaks if PM renames an operation or model.

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

**Date & Time:**
- All UTC
- ISO 8601: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (timestamps), `YYYY-MM-DD` (dates)

**General:**
- No inline module imports; all at file top
- Docstrings for public modules, classes, functions
- Test structure mirrors source within each package (`packages/<name>/src/<pkg>/foo.py` → `packages/<name>/tests/test_foo.py`)
- Explicit imports only
- Small, focused functions
