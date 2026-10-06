# Commands

Authoritative command reference for `usa-wa` — full options, exit codes, and design
rationale. The everyday subset is in [`AGENTS.md`](../AGENTS.md#common-commands).

Grouped references split out so each stays loadable on its own:

- [COMMANDS-SUCCESSION.md](COMMANDS-SUCCESSION.md) — operator succession events, committee-succession links
- [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md) — the write-free discovery probes
- [COMMANDS-ROSTER.md](COMMANDS-ROSTER.md) — the roster PDF: raw harvest + monthly edition re-check (#421/#237)
- [DEPLOYMENT-HOST.md](DEPLOYMENT-HOST.md) § Host maintenance — the disk GC and the slim ollama image (#394); plain bash, not job-harness CLIs

## Command index

Every operational CLI, grouped by the reference that documents it. Prod runs the
daily/weekly/monthly ones on systemd timers ([`AGENTS.md`](../AGENTS.md#server-lifecycle) § Server
Lifecycle); the rest are ad-hoc. `USA_WA_BIENNIUM` pins the raw harvests and the pipeline's open
biennium to a non-current one. The canonical tier's refreshes, builders, backfills and one-shot
migrations were deleted in #412 PR F.
Pinned both ways against the `run_job` entry points by
[`scripts/tests/test_command_index_drift.py`](../scripts/tests/test_command_index_drift.py):
a new CLI with no row, or a row naming a removed one, fails the suite.

**All of them run on the shared job harness (#179b)**: each takes `--json`, prints a `key=value`
summary, and writes a `job_runs` row (`GET /api/v1/health/jobs`). **Exit codes unchanged**
unless a doc below says otherwise (`0` ok / `1` failed / `2` config / `4` degraded, `3`
reserved for "aborted, took no action") — see
[MODULES-FRAMEWORK.md](MODULES-FRAMEWORK.md). Two CR #196 qualifications on that "all"
still hold: `--dry-run` is not on every one of them, and an exit-`2` config error writes
no ledger row.

### Documented in this file

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.raw_harvest` | Daily WSL SOAP set + member fan-out into the #302 raw file store (#304; no DB reads); `--root`, `--ttl-days` |
| `python -m usa_wa_adapter_pdc.raw_harvest` | Winner-cohort wires into the raw file store (#304); exit 4 = whole-source outage |
| `python -m usa_wa_adapter_sos.raw_harvest` | Filings + results wires into the raw file store (#304), both SOS sources one run. Exit 4 = a source landed nothing, **unless it is named in `ACCEPTED_OUTAGES`** — a known upstream outage exits 0 while still logging `sos_raw_harvest_accepted_outage` with its issue. An accepted source that RECOVERS exits 4 as `stale_acceptances`, which is what forces the entry's removal (#333) |
| `python -m clearinghouse_core.raw_integrity` | Raw-store integrity sweep — re-hash file objects vs manifests, rolling byte-slice + cursor (#304; weekly since #412); exit 1 = corruption, and with `--expect-objects` (the unit's) exit 4 = a missing or empty store |
| `python -m usa_wa_common.seed_jurisdictions` | Assert the locally-owned WA jurisdiction vocabulary into the table (#310); idempotent; strangers reported, never deleted |
| `python -m usa_wa_pipeline.registrar` | Cluster `proposed_links` (union-find) and apply the registry decision table (#308); also registers every staged WSL sponsor (#403), orgs (staged committees + meeting refs + structural orgs) and roles (the conformed dimension, #313) as singleton clusters; `--dry-run` previews; exit 4 = conflicts or malformed sponsor ids to triage |
| `python -m usa_wa_pipeline.adjudicate` | Merge/unmerge entities / move a key, `--note` mandatory, recorded in `registry.adjudications` (#308) |
| `python -m usa_wa_pipeline.build_warnings` | The nightly's dbt-warning gate (#412 PR E): reads the build's `run_results.json` (`--run-results`, default `data/target/run_results.json`) and exits 1 naming every node whose status is not `pass`/`success` in `not_clean`. `dbt build` exits 0 on a `warn`, so this is how a chamber vacancy or a ratchet-me-down warning reaches the operator. 4 = no file, no nodes, or a run that was not a `build`. Counted by the nightly, never an abort |
| `python -m usa_wa_pipeline.coverage_seed` | Reconcile every source's declared coverage claims (#180) into `source_coverage`, which `/sources/{slug}/coverage` serves: get-or-create each declaring source through its adapter's `provisioning`. Idempotent (writes nothing when the claims match); `--dry-run` rolls back. The nightly runs it since #412 PR E disabled the refreshes that used to |
| `python -m usa_wa_pipeline.registry_coverage` | Write-free, post-registrar: rebuilds both span families from the built duckdb's staging tables (`--db`) and gates `unregistered_spans`/`_orgs`/`_roles` at zero against the registry as the registrar left it; reports `seat_overlaps_unclipped`. Exit 1 names them in `integrity_failures`; 4 = the build holds no sponsors, roster or ballot rows. Split out of `parity_spans` (#412 PR B), deleted with its canonical oracle in PR F |
| `python -m usa_wa_pipeline.parity_citations` | Write-free coverage probe over the published citations chain, asked of the BUILT duckdb rather than a recomputation. Gated at zero: `orphan_citations` (a citation naming a resource `stg_raw_fetches` does not carry), `uncited_assignments`, `uncited_roles` (**registered** roles only) and `uncited_organizations`. Ratcheted: `uncited_persons` (baseline 2). Counted only: `structural_organizations` (definitional rows no wire attests) and `unregistered_roles` (null `entity_id` — every new seat is one for exactly one build, since the nightly builds before it registers) (#313) |
| `python -m usa_wa_pipeline.publish` | Publish versioned dataset snapshots + catalog from the built duckdb (#311); exit 1 = refused, nothing minted. Three refusals: a missing table; a row shrink past `--max-shrink` (a degraded build — override only for a verified contraction); and a contract gate (#385) — a dataset's published shape changed while its `schema_version` stood still, or the declared version sits below the published one. The contract refusal is fixed in `publish.PUBLISHED_DATASETS`, not by a flag: append a `ContractRelease` |
| `python -m usa_wa_api.serving.load` | Published datasets → the disposable Postgres `serving` schema the API reads (#313). Catalog-driven; refuses on a datapackage/table contract break, loading nothing. Nightly, after publish |

### Roster PDF

Full options, exit codes and rationale: [COMMANDS-ROSTER.md](COMMANDS-ROSTER.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.roster_pdf.raw_harvest` | Archive the WA Legislature roster PDF (1889–2025, `usa_wa_legislature_roster`) into the raw store the #302 pipeline stages from — Phase A (#421); on demand, one edition, not a sweep; exit 4 = document unlocatable or a newer edition published. Run `--dry-run --force` monthly by `usa-wa-roster-pdf-recheck.timer` as the edition check (#237) |

### Succession and committee lineage

Full options, exit codes and rationale: [COMMANDS-SUCCESSION.md](COMMANDS-SUCCESSION.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.operators.cli` | Record operator succession events — the live interjection surface (#107) |
| `python -m usa_wa_adapter_legislature.committees.succession_cli` | Record operator committee-succession links — the judgment layer (#124 C2) |

### Backup and recovery — [RECOVERY.md](RECOVERY.md)

Both hold no database credential and write no `job_runs` row (`needs_db=False`).

| Command | Purpose |
|---|---|
| `python -m usa_wa_api.backup.run` | Nightly backup (#434): the database dump to `gs://<bucket>/db/<host>/<stamp>.dump`, verified, with the registry's row counts as metadata, and the raw store's new objects and manifests mirrored under `raw/` — create-only. `--dry-run` dumps, verifies and hashes but uploads nothing. Exit 1 = either half did not ship; 2 = no `USA_WA_BACKUP_BUCKET`, or a misplaced key. Daily on `usa-wa-backup.timer`, sandboxed |
| `python -m usa_wa_api.backup.restore` | By hand, as root: `--list` the dumps; `--latest --prefix HOST` or `--object KEY` with `--download-only DIR` (fetch + prove) or `--into DB --run-as postgres` (load into an **empty** database, then check schema version, registry row counts and the published crosswalks' ULIDs); `--raw-into DIR` the raw store, `latest.json` rebuilt. Exit 1 = any refusal or failed check |

### Probes

Full options, exit codes and rationale: [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.committees.probe_extent` | Write-free: how much committee history exists (#64) |
| `python -m usa_wa_adapter_legislature.sponsors.probe_identity [--history]` | Write-free: is the WSL member Id stable (#27/#81) |
| `python -m usa_wa_adapter_legislature.probe_availability --biennium B [--log PATH] [--until YYYY-MM-DD]` | Write-free (#135): how much of biennium B's rosters WSL serves — `GetSponsors` by chamber, `GetCommittees` (`null` while it faults), committee members — appended to a JSONL log; exit 4 when a count changed **state** — faulting, empty, has rows (the news) — 0 otherwise (growth between published counts is logged only) or past `--until`. `--dry-run` measures without logging. Daily on `usa-wa-wsl-availability-probe.timer` for 2027-28 through 2027-02-28; runbook `docs/RUNBOOK-ROLLOVER.md` |

## Setup

```bash
# Dependencies (creates .venv, locks deps in uv.lock)
uv sync

# Pre-commit hook (runs ruff on commit)
uv run pre-commit install
```

**In a fresh worktree, `uv sync --locked` before the first commit.** Feature work
happens in a worktree (`docs/DEPLOYMENT-HOST.md` § Main-only checkout), and a new one
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

## Submodules

The `skills-vendor/` directory holds upstream skill repos as submodules. They are updated automatically by the `UserPromptSubmit` hook in [`.claude/settings.json`](../.claude/settings.json), but the manual commands are:

```bash
# Initialize submodules on a fresh clone
git submodule update --init --recursive

# Update vendored skills to the latest upstream main
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
```
