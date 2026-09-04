# Commands

Authoritative command reference for `usa-wa` — full options, exit codes, and design
rationale. The everyday subset is in [`AGENTS.md`](../AGENTS.md#common-commands).

Grouped references split out so each stays loadable on its own:

- [COMMANDS-SUCCESSION.md](COMMANDS-SUCCESSION.md) — operator succession events, odd-year corroboration, committee lineage
- [COMMANDS-SYNC.md](COMMANDS-SYNC.md) — Power Map reconcilers, heals, validation, provenance and integrity
- [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md) — historical harvests, span builders, one-shot migrations, write-free probes
- [COMMANDS-SEATS.md](COMMANDS-SEATS.md) — the Layer-3b seat-fact backfills: PDC identifier links (#79), WSL+SOS House Position (#101)

## Command index

Every operational & backfill CLI, grouped by the reference that documents it. Prod runs the
daily/weekly ones on systemd timers ([`AGENTS.md`](../AGENTS.md#server-lifecycle) § Server
Lifecycle); the rest are run-once / ad-hoc. Pair backfills with `USA_WA_BIENNIUM` to target
a non-current biennium.

**All 44 run on the shared job harness (#179b)**: each takes `--json`, prints a `key=value`
summary, and writes a `job_runs` row (`GET /api/v1/health/jobs`). **Exit codes unchanged**
unless a doc below says otherwise (`0` ok / `1` failed / `2` config / `4` degraded, `3`
reserved for "aborted, took no action") — see
[MODULES-FRAMEWORK.md](MODULES-FRAMEWORK.md). Two CR #196 qualifications on that "all" —
`--dry-run` is on 40 of 44, and an exit-`2` config error writes no ledger row — are in
[COMMANDS-SYNC.md](COMMANDS-SYNC.md#the-harness-contract).

### Documented in this file

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.refresh` | Daily WSL pull — committees + meeting window + member cluster |
| `python -m usa_wa_adapter_pdc.archive_refresh` | Daily PDC winner-cohort archive — Phase A of the PDC cycle (#201); exit 4 = every cohort unserved |
| `python -m usa_wa_facts_seats.pdc.refresh` | Daily PDC rebuild — `person_wa_pdc` identifier links off the archive (#69; identifier-only since #101, rebuild-only since #201) |
| `python -m usa_wa_adapter_sos.results.archive_refresh` | Daily SOS results archive — Phase A of the SOS cycle (#201); exit 4 = every cohort unserved |
| `python -m usa_wa_facts_seats.house.refresh` | Daily House Position rebuild — the WSL+SOS span builder off the archive (#101; rebuild-only since #201) |
| `python -m usa_wa_adapter_legislature.raw_harvest` | Daily WSL SOAP set + member fan-out into the #302 raw file store (#304; no DB reads); `--root`, `--ttl-days` |
| `python -m usa_wa_adapter_pdc.raw_harvest` | Winner-cohort wires into the raw file store (#304); exit 4 = whole-source outage |
| `python -m usa_wa_adapter_sos.raw_harvest` | Filings + results wires into the raw file store (#304), both SOS sources one run |
| `python -m clearinghouse_core.raw_integrity` | Raw-store integrity sweep — re-hash file objects vs manifests, rolling byte-slice + cursor (#304); exit 1 = corruption |
| `python -m clearinghouse_core.raw_export` | One-shot hash-preserving RawPayload corpus export into the raw store (#305); resumable cursor, `--reset-cursor`, mismatch = exit 1 |
| `python -m usa_wa_pipeline.parity_wsl` | Write-free parity probe: WSL staging rows vs. canonical Postgres (#306); exit 1 = unexplained divergence |
| `python -m usa_wa_pipeline.parity_pdc` | Write-free subset parity: canonical `wa_pdc` links ⊆ staging PDC winners (#307) |
| `python -m usa_wa_pipeline.registry_seed` | Seed the identity registry from canonical persons/orgs/**roles**, ULIDs preserved (#308, #313); idempotent; exit 4 = conflicts. **Run before the first registrar pass that sees roles** — otherwise it mints fresh role ULIDs and PM's #312 anchors break (docs/PIPELINE.md § Identity registry) |
| `python -m usa_wa_common.seed_jurisdictions` | Assert the locally-owned WA jurisdiction vocabulary into the table (#310); idempotent; strangers reported, never deleted |
| `python -m usa_wa_pipeline.registrar` | Cluster `proposed_links` (union-find) and apply the registry decision table (#308); also registers roles from the conformed dimension as singleton clusters (#313); `--dry-run` previews; exit 4 = conflicts to triage |
| `python -m usa_wa_pipeline.adjudicate` | Merge/unmerge entities / move a key, `--note` mandatory, recorded in `registry.adjudications` (#308) |
| `python -m usa_wa_pipeline.parity_spans` | Write-free parity: conformed tenure spans vs `canonical.assignments` **and** the derived role dimension (key + `role_type`/`name`/`qualifier`) vs `canonical.roles`. Two ratchets (`--baseline`, `--role-baseline`) plus integrity counters gated at zero; a failing run names them in `ratchet_failures`/`integrity_failures` (#309) |
| `python -m usa_wa_pipeline.parity_citations` | Write-free coverage probe over the published citations chain, asked of the BUILT duckdb rather than a recomputation. Gated at zero: `orphan_citations` (a citation naming a resource `stg_raw_fetches` does not carry), `uncited_assignments`/`uncited_roles`/`uncited_organizations`. Ratcheted: `uncited_persons` (baseline 3). `structural_organizations` counted only — definitional rows no wire attests (#313) |
| `python -m usa_wa_pipeline.parity_registry` | Write-free parity: every canonical row's key maps to its own ULID in the registry (#308) |
| `python -m usa_wa_pipeline.anchor_export` | One-time PM crosswalk seed: every `pm_*` anchor as base32 pairs + manifest (#312); read-only |
| `python -m usa_wa_pipeline.publish` | Publish versioned dataset snapshots + catalog from the built duckdb (#311); shrink gate refuses a degraded build (`--max-shrink` overrides); exit 1 = refused, nothing minted |
| `python -m usa_wa_api.serving.load` | Published datasets → the disposable Postgres `serving` schema the API reads (#313). Catalog-driven; refuses on a datapackage/table contract break, loading nothing. Nightly, after publish |

### Seat-fact backfills

Full options, exit codes and rationale: [COMMANDS-SEATS.md](COMMANDS-SEATS.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_pdc.harvest` | Historical PDC winner cohorts — archive-only, Phase A (#79) |
| `python -m usa_wa_facts_seats.pdc.build_pdc_spans` | Era-matched `person_wa_pdc` identifier links, Phase B (#79; identifier-only since #101) |
| `python -m usa_wa_facts_seats.pdc.migrate_pdc_spans` | Retire pre-#79 per-biennium PDC House rows onto spans (#79) |
| `python -m usa_wa_adapter_sos.results.harvest` | Archive WA SOS **results** cohorts (the House Position source, `usa_wa_sos_results`) — Phase A (#101) |
| `python -m usa_wa_adapter_legislature.roster_pdf.harvest` | Archive the WA Legislature roster PDF (1889–2025, `usa_wa_legislature_roster`) — Phase A (#225); one edition, not a sweep; exit 4 = document unlocatable or a newer edition published |
| `python -m usa_wa_adapter_legislature.roster_pdf.backfill` | Roster succession dates → operator events (#226); **sidecar-paused**, defers to every existing attestation, `--dry-run` rolls back; exit 4 = nothing resolved |
| `python -m usa_wa_facts_seats.house.build` | WSL+SOS House Position seat spans (2008→present) incl. #103 elimination inference, Phase B (#101) |
| `python -m usa_wa_facts_seats.house.migrate` | Superseded-collapse (#103) + re-source usa_wa_pdc House rows → usa_wa_legislature (owner role, #101) |

### Succession, corroboration, and committee lineage

Full options, exit codes and rationale: [COMMANDS-SUCCESSION.md](COMMANDS-SUCCESSION.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_sync_powermap.reconcile_committee_active` | Reconcile PM `active` vs current roster (#44; weekly) |
| `python -m usa_wa_adapter_legislature.operators.cli` | Record operator succession events — the live interjection surface (#107) |
| `python -m usa_wa_adapter_legislature.operators.invariants` | Assert chamber counts + seat occupancy; exit 1 on drift (#107; daily) |
| `python -m usa_wa_facts_seats.senate_corroboration` | Cite elected senators + assert no odd-year Senate winner lacks an open seat; exit 1 on drift (#123; daily) |
| `python -m usa_wa_facts_seats.house_corroboration` | Assert no odd-year House special winner lacks an open Position seat; `--sweep-biennia` historical audit; exit 1 on drift (#149; daily) |
| `python -m usa_wa_adapter_legislature.committees.succession_cli` | Record operator committee-succession links — the judgment layer (#124 C2) |
| `python -m usa_wa_sync_powermap.committee_event_producer` | Emit committee lifecycle windows + succession links to PM as org events (#124 C3) |
| `python -m usa_wa_adapter_legislature.committees.lineage_invariants` | Assert committee lineage coherence (INV1/INV2); exit 1 on drift (#124 C4; daily) |
| `python -m usa_wa_adapter_legislature.committees.lineage_suggest` | Advisory: rank committee succession-candidate pairs (#124 C5) |

### Power Map sync

Full options, exit codes and rationale: [COMMANDS-SYNC.md](COMMANDS-SYNC.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_sync_powermap.backfill_contact_labels` | Re-observe orgs w/ phone so PM adopts contact label (#31) |
| `python -m usa_wa_sync_powermap.reconcile_committee_names` | Committee rename → dated-name evidence (#46; weekly) |
| `python -m usa_wa_sync_powermap.reconcile_committee_meeting_names` | Joint/Other rename detection (#56; weekly) |
| `python -m usa_wa_sync_powermap.validate_committees` | Read-only local↔PM drift report (#64) |
| `python -m usa_wa_sync_powermap.heal_committee_curation` | Force-adopt PM curation for LWW-locked committees (#65) |
| `python -m usa_wa_sync_powermap.heal_assignment_clocks` | Adopt PM's clock onto LWW-skewed anchored assignments; stop churn (#102) |
| `python -m usa_wa_sync_powermap.reanchor_assignments` | Re-resolve assignment anchors PM reminted in a merge, by natural key (#283) |
| `python -m usa_wa_sync_powermap.prune_subscriptions` | Unsubscribe PM-only strangers; re-run to stale=0 (#73) |
| `python -m usa_wa_sync_powermap.retract_assignments` | Retract spurious anchored assignments on PM (`op:"retract"`) + tombstone locally; sidecar-paused (#144 Phase 2) |
| `python -m clearinghouse_core.integrity` | Provenance integrity sweep — rolling byte-slice (#54/#55; weekly) |
| `python -m usa_wa_adapter_legislature.committees.migrate_fetch_baseline` | OWNER-role provenance repair (#64) |

### Historical backfill and probes

Full options, exit codes and rationale: [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.committees.probe_extent` | Write-free: how much committee history exists (#64) |
| `python -m usa_wa_adapter_legislature.sponsors.probe_identity [--history]` | Write-free: is the WSL member Id stable (#27/#81) |
| `python -m usa_wa_adapter_legislature.meetings.harvest` | Joint/Other backfill + seed freeze (#39) |
| `python -m usa_wa_adapter_legislature.committees.ingest_seed` | No-WSL Joint/Other seed loader (#39) |
| `python -m usa_wa_adapter_legislature.sponsors.harvest` | Historical member backfill — Persons only, Phase A (#77) |
| `python -m usa_wa_adapter_legislature.sponsors.build` | Merged-span member Assignments, Phase B (#78) |
| `python -m usa_wa_adapter_legislature.sponsors.migrate_spans` | Collapse stranded party/Senate rows (3-part legacy #78-3 + superseded 4-part #97) onto merged spans (owner role) |
| `python -m usa_wa_adapter_legislature.membership.harvest` | Historical committee rosters — Persons only, Phase A (#82) |
| `python -m usa_wa_adapter_legislature.membership.build` | Merged committee-membership spans, Phase B (#82) |
| `python -m usa_wa_adapter_legislature.membership.migrate_spans` | Retire per-biennium committee rows stranded by deeper spans (#82) |
| `python -m usa_wa_adapter_legislature.migrate_role_types` | Reclassify generic `member` Roles → PM catalog slugs (`committee_member`/`party_member`) to stop the #110 no-op-gate churn |
| `python -m usa_wa_adapter_legislature.committees.harvest` | Committee historical backfill, Phase A (sub-project 3) |
| `python -m usa_wa_sync_powermap.reconcile_committee_name_chain` | Full committee rename-chain emit, Phase B (sub-project 3) |
| `python -m usa_wa_adapter_sos.filings.harvest` | Archive WA SOS votewa **filing** cohorts (candidacy metadata, `usa_wa_sos`) — Phase A (#100); closed archive, caps at 2018 (#169) |

## Setup

```bash
# Dependencies (creates .venv, locks deps in uv.lock)
uv sync

# Pre-commit hook (runs ruff on commit)
uv run pre-commit install
```

**In a fresh worktree, `uv sync --locked` before the first commit.** Feature work
happens in a worktree (`docs/DEPLOYMENT.md` § Main-only checkout), and a new one
has no `.venv`. The `import-linter` hook is `always_run: true` and invokes
`uv run --frozen --no-sync`, which — correctly, per the never-sync-in-a-unit rule
(issue #30) — creates an empty venv and installs nothing, so the first commit
fails with `error: Failed to spawn: lint-imports`. That message names neither the
cause nor the fix; this is it.

## Environment

Production secrets in `/etc/usa-wa/.env`, dev/agent secrets in `./.env` — both git-ignored, both loaded by the systemd units. Shell sessions load them manually:

```bash
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)
# In a worktree that is one file short — `.env` is git-ignored and never
# inherited. See AGENTS.md § Environment Variables (#296).
```

## Dev server

```bash
# Port 8001 — port 8000 belongs to systemd, never start uvicorn there manually
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload --log-config packages/usa-wa-api/src/usa_wa_api/log_config.json
```

Reachable at `https://usa-wa.exe.xyz:8001/` via the exe.dev proxy.

## Tests

```bash
# Unit tier (#185) — no database, own coverage gate (#198)
uv run pytest -m 'not db and not integration'

# Full suite — requires TEST_DATABASE_URL set to a non-prod database
uv run pytest

# Concurrent db-marked sessions (e.g. another worktree) serialize on a session-wide
# Postgres advisory lock (#208); a queued session waits TEST_DATABASE_LOCK_TIMEOUT
# seconds (default 600), then fails naming the situation ("another pytest session
# holds the test database") instead of silently corrupting the holder

# Single file — --no-cov: neither gate measures a slice
uv run pytest --no-cov packages/usa-wa-api/tests/test_health.py

# Integration-marked only (excluded by default) — coverage floor waived (#216):
# green exits 0, red exits non-zero, no flags needed
uv run pytest -m integration
```

`db` is applied automatically to anything resolving `test_engine`/`db_session`, by hand
where a test opens its own engine (root `conftest*.py`).

## Database migrations

Migrations require the **owner role** (DDL rights) — the DML-only `usa_wa_app`
that serves traffic cannot run them. In production, apply via the oneshot unit,
which runs `alembic upgrade head` + `scripts/grants.sql` under `DATABASE_URL_OWNER`:

```bash
sudo systemctl start usa-wa-migrate
```

Ad-hoc `alembic` commands work too, but only when `DATABASE_URL_OWNER` is in the
environment (the standard `export $(cat /etc/usa-wa/.env .env | xargs)` loads it;
`alembic/env.py` prefers it over `DATABASE_URL`):

> **`DATABASE_URL=… uv run alembic …` does not retarget the database.** `env.py`'s
> precedence is `DATABASE_URL_OWNER` → `DATABASE_URL` → `alembic.ini`, and the standard
> env load always sets the first — so a per-command `DATABASE_URL` override is silently
> ignored and the migration runs against **production**. This is how #247's revision
> reached the live schema from a feature branch: the intended scratch database had failed
> to be created (`permission denied to create database` — the owner role has no `CREATEDB`),
> and the ignored override sent the `upgrade` to prod instead. To target another database,
> override `DATABASE_URL_OWNER` itself, and check the `CREATE DATABASE` actually succeeded
> before chaining the `upgrade` onto it.

```bash
# Apply pending migrations
uv run alembic upgrade head

# Autogenerate a new revision from model diffs
uv run alembic revision --autogenerate -m "description"

# Show current head
uv run alembic current

# Show migration history
uv run alembic history
```

## Lint & format

```bash
uv run ruff check .
uv run ruff format .
```

## Systemd lifecycle

```bash
# After committing to main: restart to pick up changes
sudo systemctl restart usa-wa

# After editing deploy/usa-wa.service: reload then restart
sudo systemctl daemon-reload && sudo systemctl restart usa-wa

# Tail live logs
sudo journalctl -u usa-wa -f
```

See [DEPLOYMENT.md](DEPLOYMENT.md) § Lifecycle reference for the full unit-by-unit
restart matrix, and [`AGENTS.md`](../AGENTS.md#server-lifecycle) § Server Lifecycle
for the `--no-sync` / `uv sync --locked` deploy convention.

## Data refresh (daily)

Prod runs these on systemd timers; the forms below are the ad-hoc / backfill
surface. Pair with `USA_WA_BIENNIUM` to target a non-current biennium.

```bash
# WSL refresh — one-shot pull from CommitteeService.GetActiveCommittees, plus an additive
# current-biennium meeting-window pull for Joint/Other discovery (#39). Prod runs this daily at
# 06:00 UTC via usa-wa-wsl-refresh.timer; the form below is the manual / backfill one (pair with
# USA_WA_BIENNIUM). Also drives the member cluster: forced GetSponsors + a per-committee
# GetCommitteeMembers(current, ...) fan-out (#82), then re-drives BOTH span builders for the
# current cohort — party/Senate-seat (#78-2c) and committee membership (#82). fill_only (#65 —
# additive, never clobbers PM-curated rows). Exit 1 = the committees pull reported errors; the
# work it reached still commits — hence no --dry-run (CR #196).
python -m usa_wa_adapter_legislature.refresh

# Each daily cycle is TWO jobs since #201 — archive (adapter, Phase A) then rebuild (fact, Phase
# B) — one unit each. Flag semantics across the halves: § Archive vs rebuild, below.

# PDC cohort ARCHIVE (#201 Phase A) — archives every winner cohort the current biennium's
# membership can be decided by (#121: both House generals + the three senate-winners:<Y>), each in
# its own SAVEPOINT (a transient Socrata failure skips one cohort; a raceless year is an empty
# cohort, a success), forced past the TTL. usa-wa-pdc-archive-refresh.service, pulled in by the
# rebuild below. USA_WA_PDC_APP_TOKEN (optional). Exit 4 = every cohort unserved.
python -m usa_wa_adapter_pdc.archive_refresh

# PDC refresh (#69 + #75; IDENTIFIER-ONLY since #101, REBUILD-ONLY since #201) — re-drives
# build_pdc_spans scoped to the current biennium off that archive, emitting the person_wa_pdc
# cross-source links (House winners + #74 movers + #75 Senate). The House Position SEAT is the
# WSL+SOS builder's (usa-wa-sos-refresh, below), usa_wa_legislature-sourced (#101). Prod runs this
# daily at 06:30 UTC (after the WSL refresh) via usa-wa-pdc-refresh.timer.
python -m usa_wa_facts_seats.pdc.refresh

# SOS results ARCHIVE (#201 Phase A) — archives the current biennium's results cohorts
# (sos-legresults:<YYYYMMDD>: even seating + odd mid-biennium special, #106), SAVEPOINT-guarded and
# forced. usa-wa-sos-archive-refresh.service. Exit 4 = every cohort unserved.
python -m usa_wa_adapter_sos.results.archive_refresh

# SOS refresh (#101; REBUILD-ONLY since #201) — the daily driver of the WSL+SOS House
# state_representative Position seat: re-drives build_house_position_spans scoped to the current
# biennium -> usa_wa_legislature Position seat spans (current biennium = the open end), reading the
# WSL sponsor archive (who sits) + the SOS archive (the Position). Prod runs this daily at 06:45
# UTC (after the WSL refresh) via usa-wa-sos-refresh.timer; independent of the PDC refresh.
python -m usa_wa_facts_seats.house.refresh
```

The seat-fact historical backfills those daily rebuilds re-drive — the PDC winner
cohorts (#79) and the WSL+SOS House Position seat (#101), each with its Phase A /
Phase B / migration sequence — are in
[COMMANDS-SEATS.md](COMMANDS-SEATS.md).

## Archive vs rebuild — which half each flag governs (#201)

The daily PDC and SOS cycles are **two jobs, not one**. The adapter owns "refresh my archive"
(Phase A — a live client), the fact owns "rebuild from that archive" (Phase B — a cohort
interface). Before #201 both ran in one process, which is why `usa-wa-facts-seats` held the only
two `import-linter` exceptions in the tree.

| Half | Command | Unit | Ledger slug |
|---|---|---|---|
| PDC archive | `python -m usa_wa_adapter_pdc.archive_refresh` | `usa-wa-pdc-archive-refresh.service` | `pdc-archive-refresh` |
| PDC rebuild | `python -m usa_wa_facts_seats.pdc.refresh` | `usa-wa-pdc-refresh.service` | `pdc-refresh` |
| SOS archive | `python -m usa_wa_adapter_sos.results.archive_refresh` | `usa-wa-sos-archive-refresh.service` | `sos-archive-refresh` |
| SOS rebuild | `python -m usa_wa_facts_seats.house.refresh` | `usa-wa-sos-refresh.service` | `sos-refresh` |

- **`--force` belongs to the ARCHIVE half, and only there.** It bypasses the Source's freshness
  TTL, and the rebuild holds no cache — the builders re-derive from the archive every run and are
  idempotent. The daily archive refreshes force *by default* (the day's archive must be the day's
  wire; the dedup guard still bounds `RawPayload` growth on a byte-identical re-pull); the
  historical sweeps (`…pdc.harvest`, `…results.harvest`) expose `--force` as an opt-in flag.
  Neither rebuild has a `--force` to give.
- **`USA_WA_BIENNIUM` governs BOTH halves**, independently: it scopes which cohorts are archived
  and which biennium's spans are rebuilt. Each half resolves it on its own and warns when it names
  a closed biennium (`*_noncurrent_biennium`), so a stale pin is loud twice, not silently
  half-applied. Pin it for both when running a non-current biennium by hand.
- **`--dry-run`**: the archive halves take the harness's (they archive and roll back). Neither
  rebuild offers one — each commits through its own `session.begin()`, so the flag could only have
  lied (CR #196 finding 55).
- **Failure semantics.** Each half has its own `job_runs` row and its own `OnFailure=` alert. The
  archive half exits `4` (`EXIT_DEGRADED`) when *every* cohort was unserved — a whole-source
  outage, which pre-split exited 0 behind a WARNING nothing consumed. A failed archive does **not**
  cancel the rebuild: the rebuild unit `Wants=` its archive unit rather than `Requires=` it, so on
  a votewa/Socrata outage the fact is still re-derived from the last good archive and keeps
  tracking the WSL roster, which neither source has a part in.
- **Running one by hand.** `sudo systemctl start usa-wa-sos-refresh.service` runs *both* halves
  (the `Wants=` pulls the archive in). To rebuild without touching the source — the common case
  when debugging a span — run the rebuild module directly.

## Submodules

The `skills-vendor/` directory holds upstream skill repos as submodules. They are updated automatically by the `UserPromptSubmit` hook in [`.claude/settings.json`](../.claude/settings.json), but the manual commands are:

```bash
# Initialize submodules on a fresh clone
git submodule update --init --recursive

# Update vendored skills to the latest upstream main
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
```
