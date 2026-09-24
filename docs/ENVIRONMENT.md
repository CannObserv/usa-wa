# Environment variables

Every variable the deployment reads. The two env
files themselves — load order and what belongs in which — are in
[`AGENTS.md`](../AGENTS.md#environment-variables); the DB roles these DSNs map
to are in [DEPLOYMENT.md](DEPLOYMENT.md) § DB role topology.

Currently defined:
- `GH_TOKEN` — GitHub personal access token (used by `gh` CLI)
- `ANTHROPIC_API_KEY` — **optional**, repo-root `.env` only. Consumed by `curating-context`'s `measure-context.sh --exact`, which counts tokens via the Anthropic `count_tokens` endpoint (free to call, no tokens billed). Without it the run degrades to a calibrated offline estimate, records `tokens_exact: false`, and `record-telemetry.sh` then **refuses the ledger append** (exit 4) rather than reset the trend baseline — so a weekly curation with no key silently produces no row. A Claude Code session exports no key of its own; the script parses this one out of `.env`. See [SKILLS.md § Context budget](SKILLS.md#context-budget).
- `DATABASE_URL` — PostgreSQL connection string (app role `usa_wa_app` — DML only)
- `DATABASE_URL_OWNER` — owner-role DSN for migrations (migrate host only; `usa-wa-migrate.service` + `scripts/migrate.sh`). `alembic/env.py` prefers it over `DATABASE_URL`. Absent from the live API/sidecar units. Since #179b it is also the DSN the five one-shot **span/source migration CLIs** run on — they declare `role="owner"` to `run_job()`, which resolves it through `get_database_url("owner")` and builds a per-run engine from it (the whole run, `job_runs` ledger writes included). It never falls back to `DATABASE_URL`: the role exists precisely for deletes the app role is REVOKEd on (#54), so a fallback would trade a clear "variable is not set" for a permission error partway through a migration.
- `TEST_DATABASE_URL` — PostgreSQL connection string for the test database (test role; database name must end in `_test`)
- `TEST_DATABASE_LOCK_TIMEOUT` — seconds a queued pytest session waits for the test-DB session advisory lock (#208) before failing with "another pytest session holds the test database"; default 600
- `BUILD_ID` — git SHA stamped by the systemd unit's `ExecStartPre`; defaults to `"dev"` outside systemd
- `USA_WA_OPERATOR_TOKEN` — **retired at #313, and removed from `/etc/usa-wa/.env` on 2026-09-04.** It gated exactly one route, `POST /sync/redrive`, which retired with the rest of the mutating API. Re-driving dead-lettered `UNAVAILABLE` outbox entries is on-box only now (`python -m usa_wa_api.cli.redrive`), where shell access is the trust boundary. Nothing reads it; do not re-add it. The `/etc/usa-wa/.env.bak*` files that still carried the value were shredded the same day, so `/etc/usa-wa/` now holds `.env` alone.
- `USA_WA_BIENNIUM` — optional override for the auto-computed WA biennium label (e.g. `2025-26`) used by the WSL **and** PDC refreshes. Without it, `refresh.py` derives the biennium from the current UTC date (WA bienniums start on odd years). Useful for backfills and early-year edge cases.
- `USA_WA_PDC_APP_TOKEN` — **optional** Socrata application token for the PDC archive refresh (#69; since #201 read by `usa-wa-pdc-archive-refresh.service`, the sourcing half — the rebuild unit touches no wire), sent as the `X-App-Token` header only when set. Rate-limiting only (moves throttling from per-IP to per-app), **not** authentication — the dataset is public and readable without it, so it's not required at the once-daily single-GET volume. Register one free in a data.wa.gov profile to raise limits.
- `USA_WA_WSL_MIN_REQUEST_INTERVAL` — **optional** central courtesy floor (seconds) between any two WSL SOAP calls, across all `WSLClient` instances/services (#77). Default `0.5` (≤2 req/s); `0` disables. A harvest's `--pause-seconds` overrides it for that run via `configure_wsl_rate_limit()`; the flag defaults to `None` everywhere (#169), so an unflagged run leaves this variable in force. Protects the single WSL upstream from bursts regardless of which caller is running.
- `USA_WA_SOS_MIN_REQUEST_INTERVAL` — **optional** central courtesy floor (seconds) between any two WA SOS **filings** calls (#100), the #77 pattern applied to `eledataweb.votewa.gov`. Default `1.0` (gentle — votewa is a low-QPS government site and the harvest is a handful of GETs); `0` disables. **Live since #169**: `usa_wa_adapter_sos.filings.harvest` is the only production caller of `SOSFilingsClient` (Phase B re-parses the archive offline) and it used to call `configure_sos_rate_limit(--pause-seconds)` *unconditionally*, so the flag's own `1.0` default overwrote whatever this variable seeded at import — the knob was inert. `--pause-seconds` now defaults to `None` and only overrides when passed, so an unflagged run honours this variable.
- `USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL` — **optional** courtesy floor for the **results** source's host `results.vote.wa.gov` (#101) — the sibling of the above for the second SOS source (a *distinct* host, so its own limiter + knob). Read by the sourcing halves only (the harvest and, since #201, `usa-wa-sos-archive-refresh.service`). Default `1.0`; `0` disables; `configure_results_rate_limit()` maps a harvest's `--pause-seconds`, which since #169 defaults to `None` (unset leaves this variable in force).
- `USA_WA_RAW_ROOT` — root directory of the #302 raw-tier file store (#304): content-addressed wire objects + per-run manifests under `<root>/<source-slug>/`, written by the three `raw_harvest` CLIs and verified by `python -m clearinghouse_core.raw_integrity`. Default `raw/` relative to the invoker's cwd — set it to an absolute path (e.g. `/home/exedev/usa-wa/raw`) in `/etc/usa-wa/.env` (the harvest timers ship in `usa-wa-pipeline.timer`, #311), or a worktree run scatters stores per checkout.
- `USA_WA_PIPELINE_HERMETIC` — `1` lets the conformed crosswalk **and span** models build with no database (no registry crosswalk, no operator events) (set by `scripts/dbt-gate.sh` and the dbt tests only); any other state makes a missing `DATABASE_URL` fail the `dbt build` loudly (#302 CR). Never set it in `/etc/usa-wa/.env`.
- `USA_WA_PIPELINE_DB` — the dbt-duckdb database file the #302 pipeline builds into (#303, read by `packages/usa-wa-pipeline/dbt/profiles.yml`). Default `data/pipeline.duckdb` relative to the invoker's cwd; the commit gate (`scripts/dbt-gate.sh`) and the test suite always override it with a throwaway path.
- `USA_WA_DATASETS_ROOT` — root of the published-dataset tree (#311): the publisher writes immutable `<name>/<version>/` dirs + `catalog.json` here, and the API serves it at `/datasets/*` (resolved per request). Default `data/datasets` relative to the invoker's cwd / the service's WorkingDirectory — the primary checkout, so publisher and API agree without configuration.
- `USA_WA_ALERT_EMAIL` — recipient for oneshot failure alerts (#49). Consumed by `scripts/notify-failure.sh` (the `usa-wa-notify-failure@.service` `OnFailure=` handler). Must be **you / an exe.dev team member** (gateway recipient allow-list). The script **fails closed** if unset, so set it in `/etc/usa-wa/.env` to arm alerting. See [DEPLOYMENT.md](DEPLOYMENT.md) § Failure alerting.

### Host maintenance (#394) — `scripts/disk-gc.sh`, `scripts/slim-ollama-image.sh`

Both read their configuration from the environment and **neither is wired to an
`EnvironmentFile=`**: `usa-wa-disk-gc.service` deliberately loads no env file,
because the whole point of that unit is to still run when the repo and its
configuration are in a bad state. So these are defaults-in-the-script,
overridable for an ad-hoc run or by editing the unit — not knobs to set in
`/etc/usa-wa/.env`, where they would have no effect on the timer.

Every numeric one is validated at startup and a bad value **exits 2 rather than
running** (#394 CR 19): unvalidated, a typo made bash skip the check and carry
on with that guard disabled, which for a tool whose verb is `rm -rf` is the
wrong direction to fail in.

- `DISK_GC_GRACE_MINUTES` — minutes a candidate must have been untouched before
  it may be removed; default `60`, `0` disables. This is the guard liveness
  cannot provide: a tree being installed *right now* is named by no running
  process, because the process that will run out of it does not exist yet. With
  ~457 MB `_npx` installs happening at session start (#389) and the timer firing
  daily at 05:45, a session starting a minute earlier would otherwise have its
  half-written install deleted under it.
- `DISK_GC_WARN_BYTES` / `DISK_GC_FAIL_BYTES` — free-space thresholds; defaults
  2 GiB / 1 GiB. Below the fail threshold the run exits 1 into the
  `OnFailure=` alert chain (#49). Sized so the floor sits above one worktree's
  venv (~225 MB), since #394 measured 1.3 G → 565 M in 28 hours of ordinary
  session work — a threshold with less headroom fires after the damage.
- `DISK_GC_MOUNT` — filesystem to measure; default `/`.
- `DISK_GC_VSCODE_ROOT`, `DISK_GC_EXTENSIONS_ROOT`, `DISK_GC_PLUGIN_ROOT`,
  `DISK_GC_NPX_ROOT`, `DISK_GC_REPO` — the five trees it scans; defaults
  `$HOME/.vscode-server`, `$HOME/.vscode-server/extensions` (#399; follows
  `DISK_GC_VSCODE_ROOT`), `$HOME/.claude/plugins`, `$HOME/.npm/_npx`,
  `/home/exedev/usa-wa`. The unit sets `Environment=HOME=/home/exedev` so the
  `$HOME` ones resolve under systemd.
  Redirected at tmp dirs by the test suite, which is how it never touches a real
  host.
- `DISK_GC_PROC_ROOT` — where the plugin-cache in-use check reads a marker's
  `<pid>/stat` (#407); default `/proc`. Test-only: the process-table snapshot
  always reads the real `/proc`. A root without `self/stat` is refused, not
  trusted — under a wrong root every live session would read as dead.
- `DISK_GC_DOCKER` — the `docker` binary used for the ollama slim-label check;
  default `docker`, and an **empty value disables the check** (absence of a
  container stack is not damage).
- `SLIM_DOCKER`, `SLIM_CURL` — the two binaries the ollama rebuild drives;
  stubbed by its tests so nothing reaches a real daemon.
- `SLIM_GPU_GLOB` — path glob whose matching means this host has an accelerator
  and must not be stripped; default `/dev/nvidia*`, empty disables. The refusal
  exists because the CUDA/ROCm/MLX runtimes are dead weight *here* and the fast
  path on a GPU host.
- `SLIM_MIN_FREE_BYTES` — headroom required before rebuilding; default 2 GiB.
  `docker export | docker import` materialises the replacement while the
  original 8 GB image still exists.
- `SLIM_MOUNT`, `SLIM_OLLAMA_PORT`, `SLIM_OLLAMA_MODEL`, `SLIM_EXPECTED_DIMS` —
  default `/`, `11435`, `nomic-embed-text`, `768`. The last two are the
  post-rebuild verification: the model must be present and embedding at the
  expected dimensionality before the original image is discarded.

### Nightly pipeline — `scripts/pipeline-nightly.sh`

- `PIPELINE_NIGHTLY_ROOT`, `PIPELINE_NIGHTLY_UV` — the checkout the chain `cd`s
  into and the `uv` command every stage runs through; defaults
  `/home/exedev/usa-wa` and `/usr/local/bin/uv run --frozen --no-sync`.
  **Test-only** (#331 CR 6): the suite points them at a tmp dir and a stub `uv`,
  and refuses to run the script at all if they are missing — unset, it runs the
  real harvests, build and publish. `usa-wa-pipeline.service` sets neither.

The PM sidecar's own tunables (`SidecarSettings` — `POWERMAP_BASE_URL`, `POWERMAP_API_KEY`, the drain/replay/reconcile cadences and the request-rate governor) were documented here until usa-wa#314 deleted the sidecar. Nothing reads them; they can be removed from `/etc/usa-wa/.env` — and `POWERMAP_API_KEY` **should** be, since a live credential nothing uses is a credential nobody rotates.
