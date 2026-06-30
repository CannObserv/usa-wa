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
      runner.py       — AdapterRunner: cache-or-fetch decision, idempotent upsert, provenance writing (derives FetchEvent.content_hash = sha256(RawPayload.body) — the #54 integrity baseline, single chokepoint)
      integrity.py    — provenance integrity sweep (#54): `python -m clearinghouse_core.integrity` re-hashes RawPayload bodies vs FetchEvent.content_hash; exit 1 on mismatch (corruption/tamper); NULL baselines = unbaselined, skipped. Weekly timer + OnFailure alert
      seed_manifest.py — frozen-seed tamper-evidence convention (#54): writes/verifies `.sha256` (sha256sum format) + `.meta.json` sidecars for checked-in seed files; `verified_digest()` is the ingest seam — verifies a seed then returns the raw digest a loader writes into FetchEvent.content_hash (git is the in-repo evidence; sidecars are for ingest outside git)
      db/             — ULID SQLAlchemy column type (see db/ulid.md for rationale)
      database.py     — Async engine + session factory
      config.py       — Settings / env access (pydantic-settings)
      logging.py      — configure_logging() + get_logger()
  clearinghouse-domain-legislative/   — Layer 2: legislative-government model (state/federal)
    src/clearinghouse_domain_legislative/
                      — Bill, Legislator, BillAction, StatuteSection, etc. (skeletoned step 7)
      identity.py     — Person/Organization/Role/Assignment + LifecycleMixin (archived_at + deleted_at tombstones — PM archived/deleted axis split, #38/#42) + Organization.active (PM's third axis: operational live/dissolved domain flag — orgs-only, NOT a live-read gate, #43) + OrganizationName (dated name variants mirrored from PM `OrgName`/power-map#239; `Organization.name` stays the resolved current scalar, this child table is the history/association surface, #45) + OrganizationAcronym (acronym variants mirrored from PM `OrgAcronym` — list distinct from names, no type/dated window; `Organization.acronym` stays the resolved current scalar, #47)
      queries.py      — live_only(): read-side liveness guardrail (archived_at + deleted_at IS NULL) every live read routes through (#38/#42)
  clearinghouse-sync-powermap/        — Layer 1-adjacent: portable Power Map sync engine (sibling-reusable)
    src/clearinghouse_sync_powermap/
      descriptors.py  — EntityDescriptor contract (per-entity sync behaviour; zero usa-wa imports)
      engine.py       — SyncEngine: changes-feed + reconcile reads, LWW, outbox worker, backoff, merge-orphan anchor self-heal (#36) + merged_into generic re-resolution (#37)
      client.py       — PowerMapClient Protocol + value types (ObservationResult, ChangePage…)
      models.py       — sync-schema OutboxEntry + SyncState + EnrichFingerprint (delivery ledger + feed cursor + enrich re-propagation stamp)
      testing.py      — shipped test doubles (FakeEntity/Descriptor/Client) for this + sibling tests
      pmclient.py     — GeneratedPowerMapClient: adapts the generated SDK to the PowerMapClient Protocol
  powermap-client/                    — GENERATED OpenAPI client for Power Map (do not hand-edit)
                      — openapi-python-client output; excluded from ruff/coverage/pre-commit.
                        Regenerate when PM's API changes (see "Regenerating the PM client" below).
  usa-wa-adapter-legislature/         — Layer 3: WA Legislature SOAP source mapping
    src/usa_wa_adapter_legislature/
      adapter.py      — WALegislatureAdapter(BaseAdapter): discover/fetch_one/normalize; dispatches two resources — committees:<biennium> (CommitteeService) and committee-meetings:<begin>:<end> (CommitteeMeetingService, #39), normalize routes by service URL
      synthesis.py    — pure functions emitting canonical-row dicts for anchors WSL doesn't expose (legislature/chamber/biennium/regular)
      bootstrap.py    — bootstrap_synthetic_anchors: idempotent ON CONFLICT DO NOTHING upserts of the 6 anchor rows; returns BootstrapAnchors
      transport.py    — WSLClient: per-service zeep wrapper with lazy WSDL load; SOAP calls via asyncio.to_thread. fetch_active_committees + fetch_committee_meetings return WireFetch (parsed records + pristine SOAP wire for archival, #54)
      meeting_windows.py — biennium → (begin, end) window + committee-meetings:<begin>:<end> resource-id keying (#39); once-per-window cache key for docket frugality
      normalize/      — per-resource normalizers. committees.py: WSL Committee → Organization (House/Senate → chamber, Joint → legislature; org_type='committee'). committee_meetings.py: meeting refs → Joint/`Other` Organizations (#39) — dedup by stable Id, name=LongName verbatim, short_name=Name, org_type='other', parent=legislature; House/Senate skipped (CommitteeService's domain). parent_for_agency shared (extended for 'Other'). Local `name` is the verbatim double-prefixed LongName *as produced* (the read mirror still adopts PM's curated canonical), while the PM-emitted name is the clean `short_name` for org_type='other' (`OrganizationDescriptor.observed_name`, #61). parent_for_agency + clean_field (normalize/fields.py) shared with committees.py
      committee_seed.py — frozen Joint/`Other` seed (de)serialization (deterministic bytes for stable hashing); DEFAULT_SEED_PATH = data/joint_other_committees_seed.json
      harvest_committee_meetings.py — backfill CLI (#39): sweep a biennium range through the runner (archive wire + upsert org_type='other'), then freeze the deduped cohort to the seed + seed_manifest sidecars. Closed windows = cache hits on re-run
      ingest_committee_seed.py — no-WSL seed loader (#39): verified_digest gates the bytes → synthetic FetchEvent.content_hash + archived RawPayload, fill-only upsert (seed is a floor, not an authority)
      refresh.py      — `python -m usa_wa_adapter_legislature.refresh` CLI entrypoint; biennium-from-date with USA_WA_BIENNIUM override. Daily run also pulls the current biennium's meeting window for additive Joint/`Other` discovery (best-effort; window-absence ≠ retirement, #39)
  usa-wa-api/                         — Layer 4: WA deployment (FastAPI + MCP + REST)
    src/usa_wa_api/api/
      main.py         — App factory, lifespan, router registration
      deps.py         — FastAPI dependencies (DB session, auth)
    tests/            — API tests; conftest defines savepointed db_session + AsyncClient
  usa-wa-sync-powermap/               — Layer 4: PM sync deployment binding + sidecar daemon
    src/usa_wa_sync_powermap/
      descriptors/    — concrete EntityDescriptors (jurisdiction, organization, role, person, assignment) — full identity cluster + PM-first match cascade + enrich-on-match; `events.py` is the entity-event sub-resource read-mirror (person/org `fetch_record` pulls `/{id}/events`, `upsert_from_pm` mirrors via `sync_entity_events`); `org_names.py` is the dated-name read-mirror (org `upsert_from_pm` mirrors the embedded `OrgDetail.names[]` via `sync_org_names` → `OrganizationName`, #45); `org_acronyms.py` is the sibling acronym read-mirror (org `upsert_from_pm` mirrors the embedded `OrgDetail.acronyms[]` via `sync_org_acronyms` → `OrganizationAcronym`, #47)
      registry.py     — build_descriptors() — the entity set the sidecar syncs
      sidecar.py      — Sidecar: per-cycle tick (feed → reconcile → sweep → drain) + isolated run loop
      config.py       — SidecarSettings (POWERMAP_BASE_URL, POWERMAP_API_KEY)
      reconcile_committee_active.py — one-shot producer CLI (#44): diffs the produced committee cohort against `CommitteeService.GetCommittees(biennium)` and reconciles PM `active` both ways — `active=false` for committees the roster dropped, `active=true` for ones that reappear (reactivation self-heals a modest partial-pull false retirement on the next clean run). Guarded by an empty-pull check + cohort floor (denominator = active cohort); skips archived/deleted/unanchored; emit-only (PM stays authority for `active`, mirrors it back — no local write). Weekly timer (Sun 07:00 UTC, #48) + ad-hoc; out-of-band from routine sync (`to_observation` keeps `active` out, #43)
      reconcile_committee_names.py — one-shot producer CLI (#46): the write-side sibling of #45's read mirror. Detects a WSL committee **rename** (stable `Id`, changed `LongName`) by diffing `GetCommittees(current)` vs `GetCommittees(prior)` on `normalize_name(LongName)` — WSL's own raw name, **not** the PM-resolved `Organization.name` scalar (which would false-fire on PM canonicalisation and miss round-tripped renames). Emits windowed dated-name evidence via `OrganizationDescriptor.to_names_observation` (prior name `effective_end` = biennium-start boundary; new name `effective_start` = same, open end). Guarded by empty-pull (either roster) + low-overlap (`--min-overlap-fraction`, default 0.5 — stable WSL Ids mean a healthy diff overlaps near-totally; a thin overlap = wrong-biennium pull, which would otherwise read as a hollow "renamed: 0") + rename-storm floor (`--max-rename-fraction`, default 0.34); skips unanchored + the live-cohort-absent (counted **hidden** = archived/deleted-but-produced vs **unproduced** = never-produced/other-source); emit-only (PM curates `is_canonical`, #45 read mirror brings windows back — no local write). Weekly timer (Sun 07:30 UTC, #53) + ad-hoc; `--dry-run` previews
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
| WSL refresh (daily) | oneshot + timer | — | `systemctl` (`usa-wa-wsl-refresh.timer` → `.service`; 06:00 UTC). Pulls committees **and** the current-biennium meeting window for additive Joint/`Other` discovery (#39) |
| Committee active reconcile (weekly) | oneshot + timer | — | `systemctl` (`usa-wa-reconcile-committee-active.timer` → `.service`; Sun 07:00 UTC) |
| Committee rename detection (weekly) | oneshot + timer | — | `systemctl` (`usa-wa-reconcile-committee-names.timer` → `.service`; Sun 07:30 UTC) |
| Provenance integrity sweep (weekly) | oneshot + timer | — | `systemctl` (`usa-wa-integrity-sweep.timer` → `.service`; Sun 08:00 UTC) |
| Failure alerts | templated oneshot | — | `OnFailure=` → `usa-wa-notify-failure@.service` |
| API (dev) | FastAPI | 8001 | manual uvicorn |

`8001` = `8000 + 1`. The exe.dev proxy transparently forwards ports 3000–9999; the dev server is reachable at `https://usa-wa.exe.xyz:8001/`.

### Failure alerting (#49)

The unattended oneshots fail silently on a headless box — a `failed` state in the
journal nobody is watching. Each failable oneshot (`usa-wa-migrate`,
`usa-wa-wsl-refresh`, `usa-wa-reconcile-committee-active`,
`usa-wa-reconcile-committee-names`, `usa-wa-integrity-sweep`) carries
`OnFailure=usa-wa-notify-failure@%n.service`, so systemd starts the templated
handler on a non-zero exit **or** a `TimeoutStartSec=` hang. `%n` (the failing
unit's full name) becomes the handler's instance.

[`deploy/usa-wa-notify-failure@.service`](deploy/usa-wa-notify-failure@.service)
runs [`scripts/notify-failure.sh`](scripts/notify-failure.sh), which emails the
operator via the **exe.dev email gateway** (`POST
http://169.254.169.254/gateway/email/send`, a documented VM feature — no MTA/SMTP
creds needed). The reconcile exit-code contract (#44: 1 rejected / 2 auth / 3
guardrail abort) is surfaced **in the subject line** so a mass-retirement abort is
triageable without opening the journal. Recipient is `USA_WA_ALERT_EMAIL`
(`/etc/usa-wa/.env`); the script **fails closed** if it's unset — set it before
relying on alerts. The handler has no `OnFailure=` on itself (a failed send must
not recurse); a dropped alert still leaves the failure in the journal. The
serving units (`usa-wa`, `sync-powermap`) restart in place via `Restart=` and so
don't route through this one-shot alert.

### DB role topology (defense-in-depth, issue #22)

DDL and DML rights are split across roles so a misconfigured DSN can't migrate/drop the live DB:

| Role | Rights | Used by |
|---|---|---|
| `usa_wa_owner` | owns all tables/sequences; CREATE/ALTER/DROP | `alembic upgrade head` only — the `usa-wa-migrate.service` oneshot |
| `usa_wa_app` | SELECT/INSERT/UPDATE/DELETE only (no DDL) | live API, sync sidecar, WSL refresh timer, on-box CLIs |
| `usa_wa_test_owner` | owns the **separate** `usa_wa_test` database; DDL | `TEST_DATABASE_URL` — the suite owns its own schema lifecycle (`create_all`/drop per session) |

- `DATABASE_URL` (app role) serves; `DATABASE_URL_OWNER` (owner role, migrate host only) migrates. `alembic/env.py` prefers `DATABASE_URL_OWNER` when set, else `DATABASE_URL`.
- [`scripts/grants.sql`](scripts/grants.sql) is the version-controlled source of truth for grants — idempotent, re-applied after every migration by [`scripts/migrate.sh`](scripts/migrate.sh). `ALTER DEFAULT PRIVILEGES` means new tables auto-grant DML to the app role. **Add new schemas to it** when a migration introduces one.
- Provision prod once as superuser: `psql -d usa_wa -v reassign_from=usa_wa -f scripts/grants.sql` (then per-role `ALTER ROLE … PASSWORD` out-of-band; passwords are never committed).
- The **test DB** needs only its role + ownership — do **not** run `grants.sql` against it (its schemas don't exist until the suite creates them, so the schema-grant steps would error). Provision with: `psql -c "CREATE ROLE usa_wa_test_owner LOGIN PASSWORD '…'"` then `ALTER DATABASE usa_wa_test OWNER TO usa_wa_test_owner`.
- Both the API lifespan and the sidecar log a startup fingerprint (`current_user` + `current_database`) — role/DB confusion shows up in the first `journalctl` line.

## Server Lifecycle

**Port 8000 belongs to systemd.** Never start uvicorn manually on port 8000.

**Deploy convention: units never sync the venv (issue #30).** Every systemd
entrypoint runs `uv run --frozen --no-sync` (`usa-wa.service`,
`usa-wa-sync-powermap.service`, `usa-wa-wsl-refresh.service`,
`usa-wa-reconcile-committee-active.service`,
`usa-wa-reconcile-committee-names.service`,
`usa-wa-integrity-sweep.service`, `scripts/migrate.sh`).
`--no-sync` runs against the installed venv as-is; `--frozen` skips re-locking.
So unit start never mutates the environment — the daily WSL refresh timer can't
silently apply a dependency change a `git pull` landed in `uv.lock`. (Note:
`--frozen` *alone* would not prevent this — it still syncs the venv to the lock;
`--no-sync` is the flag that stops it.) **Dependency changes land only via a
deliberate `uv sync --locked` after a pull that touches `uv.lock`:**

```bash
git pull
uv sync --locked                       # reconcile venv ⇄ uv.lock deliberately
sudo systemctl restart usa-wa-migrate  # if DB models changed (restart, not start — see note)
sudo systemctl restart usa-wa usa-wa-sync-powermap
```

`uv sync` here uses `--locked` (not `--frozen`): it additionally asserts
`uv.lock` is consistent with `pyproject.toml`, catching a committed lock that
went stale — a deploy-time integrity check worth failing on. Units stay on
`--frozen` so a lock/pyproject drift can't wedge the daily timer.

If the venv is missing a locked dependency, units fail loudly at import — the
intended signal to run `uv sync`. **First provision (or after a venv wipe)
requires a plain `uv sync`** — `--no-sync` units can't start against an absent
`.venv`.

| Situation | Action |
|---|---|
| Code committed to main | `sudo systemctl restart usa-wa` (run `uv sync --locked` first if `uv.lock` changed — units are `--no-sync`; see convention above) |
| Testing a worktree/branch | `uv run uvicorn ... --port 8001 --reload` |
| Debugging the live service | `sudo journalctl -u usa-wa -f` |
| After editing `deploy/usa-wa.service` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa` |
| After editing `deploy/usa-wa-wsl-refresh.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-wsl-refresh.timer` |
| After editing `deploy/usa-wa-reconcile-committee-active.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-reconcile-committee-active.timer` |
| After editing `deploy/usa-wa-reconcile-committee-names.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-reconcile-committee-names.timer` |
| After editing `deploy/usa-wa-integrity-sweep.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-integrity-sweep.timer` |
| After editing `deploy/usa-wa-notify-failure@.service` | `sudo systemctl daemon-reload` (templated `OnFailure=` handler — nothing to restart; next failure picks it up) |
| After DB model changes | `sudo systemctl restart usa-wa-migrate` (runs alembic + grants under the owner role), then restart usa-wa — run `uv sync --locked` first if `uv.lock` changed (`migrate.sh` is `--no-sync`). **`restart`, not `start`** — the unit is a `RemainAfterExit` oneshot, so once it's `active (exited)` from an earlier migrate this boot, `start` is a silent no-op (exits 0, applies nothing). |
| Run the WSL refresh now (ad-hoc) | `sudo systemctl start usa-wa-wsl-refresh.service` |
| Run the committee active reconcile now (ad-hoc) | `sudo systemctl start usa-wa-reconcile-committee-active.service` |
| Run the committee rename detection now (ad-hoc) | `sudo systemctl start usa-wa-reconcile-committee-names.service` |
| Run the provenance integrity sweep now (ad-hoc) | `sudo systemctl start usa-wa-integrity-sweep.service` |

**Validating unit edits (#51).** A path-filtered pre-commit hook
(`systemd-verify-units` → [`scripts/verify-units.sh`](scripts/verify-units.sh))
runs `systemd-analyze verify` on any changed `deploy/*.{service,timer}`. It
fails on a non-zero exit **and** on stderr warning markers (`Unknown key name`,
`Unknown section`, `ignoring`, …), because `systemd-analyze` exits 0 on
unknown/misspelled directives — a plain `$?` gate would pass them. Catches:
directive/section typos, malformed syntax, nonexistent `ExecStart=` binaries.
Does **not** catch misspelled `After=`/`Before=` ordering deps (systemd treats
ordering against absent units as legitimate) — that gap is closed instead by
[`scripts/tests/test_unit_ordering.py`](scripts/tests/test_unit_ordering.py)
(#52), which asserts the intended `After=`/`Before=` graph as data and
cross-checks the on-disk unit set so a new unit forces an explicit ordering
decision. No-ops where `systemd-analyze` is
absent. Because `verify` resolves absolute `ExecStart=` paths
(`/usr/local/bin/uv`) and `User=exedev` against the *local* box, off-VM it can
false-**fail** even with `systemd-analyze` present — a failure off-VM means "run
it on the VM," not "your unit is broken." Run ad-hoc:
`./scripts/verify-units.sh deploy/*.service deploy/*.timer`.

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
- `USA_WA_ALERT_EMAIL` — recipient for oneshot failure alerts (#49). Consumed by `scripts/notify-failure.sh` (the `usa-wa-notify-failure@.service` `OnFailure=` handler). Must be **you / an exe.dev team member** (gateway recipient allow-list). The script **fails closed** if unset, so set it in `/etc/usa-wa/.env` to arm alerting. See § Failure alerting.

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

# Database migrations (need the owner role — see § DB role topology)
# prod: sudo systemctl restart usa-wa-migrate (restart, not start — RemainAfterExit
#       oneshot no-ops on start once already active); ad-hoc alembic needs DATABASE_URL_OWNER
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "description"

# FastAPI dev server
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload

# WSL refresh — one-shot pull from CommitteeService.GetActiveCommittees, plus an
# additive current-biennium meeting-window pull for Joint/Other discovery (#39).
# Prod runs this daily at 06:00 UTC via the usa-wa-wsl-refresh.timer systemd
# unit; the command below is the manual / backfill form (pair with USA_WA_BIENNIUM).
python -m usa_wa_adapter_legislature.refresh

# Joint/Other committee backfill (#39) — sweep CommitteeMeetingService.GetCommitteeMeetings
# over a biennium range (the only source of Joint/Other committees), archiving the pristine
# SOAP wire and upserting org_type='other' rows, then FREEZE the deduped durable cohort to
# data/joint_other_committees_seed.json (+ .sha256/.meta.json sidecars). Hits live WSL (one
# POST per window) AND mutates the DB — not read-only; --dry-run still upserts but skips the
# seed write. Closed windows are cache hits on re-run. Commit the produced seed.
python -m usa_wa_adapter_legislature.harvest_committee_meetings --from-biennium 2023-24 --to-biennium 2025-26

# Joint/Other seed ingest (#39) — the no-WSL counterpart: materialize the frozen cohort on a
# fresh deploy. verified_digest gates the seed bytes (fails closed on a sidecar mismatch),
# writes a synthetic hashed FetchEvent + archived RawPayload, and fill-only upserts (existing
# rows untouched — the seed is a floor, not an authority). Needs the committed seed file.
python -m usa_wa_adapter_legislature.ingest_committee_seed

# Contact-label backfill (#31) — re-observation of produced orgs holding a phone,
# so PM adopts the synthesized contact display_label. Idempotent + re-runnable;
# --dry-run counts the cohort without submitting. No operator token (shell = trust boundary).
# Since #34 the sidecar self-heals carry-field drift on its own (anchored-cohort
# reconcile re-enqueues an ENRICH on a local-fingerprint mismatch), so this is now a
# force-push convenience, not the only recovery path.
python -m usa_wa_sync_powermap.backfill_contact_labels --dry-run
python -m usa_wa_sync_powermap.backfill_contact_labels

# Committee active-flag reconciliation (#44) — reconciles PM `active` for WSL committees
# against the current biennium's `GetCommittees(biennium)` roster: `active=false` for the
# absent, `active=true` for the returning (reactivation self-heals a transient partial-pull
# false retirement next cycle). Explicit-membership diff (not current-only
# GetActiveCommittees), guarded by an empty-pull abort + a cohort floor (--max-absent-fraction,
# default 0.34) so a partial WSL pull can't mass-retire. Skips archived/deleted/unanchored;
# emit-only (PM mirrors `active` back). Idempotent; no operator token (shell = trust boundary).
# Prod runs this weekly (Sun 07:00 UTC) via usa-wa-reconcile-committee-active.timer (#48);
# the forms below are the manual / backfill / dry-run surface.
# --dry-run previews the diff. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_active --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_active --biennium 2025-26

# Committee rename detection (#46) — write-side sibling of #45's read mirror. Diffs
# `GetCommittees(current)` vs `GetCommittees(prior)` on the stable `Id`; a changed
# `normalize_name(LongName)` is a rename. Emits windowed dated-name evidence (prior name
# effective_end = biennium-start boundary; new name effective_start = same, open end) so PM
# curates is_canonical and the #45 read mirror brings the windows back — emit-only, no local
# write. Diffs WSL's RAW LongName, not the PM-resolved Organization.name scalar (which would
# false-fire on PM canonicalisation + miss round-tripped renames). Guarded by empty-pull
# (either roster) + low-overlap (--min-overlap-fraction, default 0.5; stable WSL Ids → a real
# diff overlaps heavily, so a thin overlap = wrong-biennium pull) + rename-storm floor
# (--max-rename-fraction, default 0.34). Skips unanchored + live-cohort-absent (hidden vs
# unproduced). Idempotent; no operator token (shell = trust boundary).
# Prod runs this weekly (Sun 07:30 UTC) via usa-wa-reconcile-committee-names.timer (#53),
# staggered 30 min off the active reconcile; the forms below are the manual / dry-run surface.
# --dry-run previews. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_names --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_names --biennium 2025-26

# Provenance integrity sweep (#54) — re-hashes every stored RawPayload body against
# its FetchEvent.content_hash baseline; a divergence is corruption/tamper at rest.
# Read-only (app role, SELECT only); NULL baselines (pre-#54 legacy) are counted as
# "unbaselined", never a mismatch. Exit 0 clean / 1 mismatch (the non-zero the #49
# OnFailure handler emails on). Prod runs this weekly (Sun 08:00 UTC) via
# usa-wa-integrity-sweep.timer; --limit N caps the scan for a quick partial check.
python -m clearinghouse_core.integrity
python -m clearinghouse_core.integrity --limit 500
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
