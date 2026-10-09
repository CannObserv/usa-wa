# Environment variables

Every variable the deployment reads. The two env
files themselves — load order and what belongs in which — are in
[`AGENTS.md`](../AGENTS.md#environment-variables); the DB roles these DSNs map
to are in [DEPLOYMENT.md](DEPLOYMENT.md) § DB role topology.

Currently defined:
- `GH_TOKEN` — GitHub personal access token (used by `gh` CLI)
- `ANTHROPIC_API_KEY` — **optional**, repo-root `.env` only. Consumed by `curating-context`'s `measure-context.sh --exact`, which counts tokens via the Anthropic `count_tokens` endpoint (free to call, no tokens billed). Without it the run degrades to a calibrated offline estimate, records `tokens_exact: false`, and `record-telemetry.sh` then **refuses the ledger append** (exit 4) rather than reset the trend baseline — so a weekly curation with no key silently produces no row. A Claude Code session exports no key of its own; the script parses this one out of `.env`. See [SKILLS.md § Context budget](SKILLS.md#context-budget).
- `SOCRATICODE_SPEC` — the npm spec the SocratiCode plugin's session server launches (#415), `socraticode@1.16.0`; unset, it floats on `socraticode@latest` and installs at session start. Declared in `.claude/settings.json` `env`, which `preflight.sh` compares against; what actually reaches the launch is the host's VS Code machine setting `claudeCode.environmentVariables`. Must match the driver's pre-install — a re-pin changes all three and first warms the new spec's npx tree under the cap, or the next session start installs it uncapped: [SOCRATICODE.md § The server is pinned](SOCRATICODE.md#the-server-is-pinned-not-installed-per-launch-389).
- `DATABASE_URL` — PostgreSQL connection string (app role `usa_wa_app` — DML only)
- `DATABASE_URL_OWNER` — owner-role DSN for migrations (migrate host only; `usa-wa-migrate.service` + `scripts/migrate.sh`). `alembic/env.py` prefers it over `DATABASE_URL`. Absent from the live API unit. A job that declares `role="owner"` to `run_job()` (#179b) resolves it through `get_database_url("owner")` for a per-run engine, never falling back to `DATABASE_URL`; no job declares it since #412 PR F deleted the one-shot span/source migration CLIs that did.
- `TEST_DATABASE_URL` — PostgreSQL connection string for the test database (test role; database name must end in `_test`)
- `TEST_DATABASE_LOCK_TIMEOUT` — seconds a queued pytest session waits for the test-DB session advisory lock (#208) before failing with "another pytest session holds the test database"; default 600
- `USA_WA_DEPLOY_BRANCH` — **optional** branch the #87 guard (`scripts/assert-main-checkout.sh`) expects the prod checkout on; default `main`. For a non-standard host only. [DEPLOYMENT-HOST.md](DEPLOYMENT-HOST.md) § Main-only checkout
- `USA_WA_DEPLOY_ROOT` — **optional** checkout root the #279 guard (`scripts/assert-venv-integrity.sh`) holds the venv's editable installs to; default `/home/exedev/usa-wa`. For a non-standard host only. [DEPLOYMENT-HOST.md](DEPLOYMENT-HOST.md) § Shared-venv integrity
- `BUILD_ID` — git SHA stamped by the systemd unit's `ExecStartPre`; defaults to `"dev"` outside systemd
- `USA_WA_OPERATOR_TOKEN` — **retired at #313, and removed from `/etc/usa-wa/.env` on 2026-09-04.** It gated exactly one route, `POST /sync/redrive`, which retired with the rest of the mutating API. The outbox it re-drove retired with the sidecar (#314). Nothing reads it; do not re-add it. The `/etc/usa-wa/.env.bak*` files that still carried the value were shredded the same day, so `/etc/usa-wa/` now holds `.env` alone.
- `USA_WA_BIENNIUM` — optional override for the auto-computed WA biennium label (e.g. `2025-26`) used by the three raw harvests (WSL, PDC, SOS), and by the #302 pipeline through `usa_wa_pipeline.conformed.spans.current_biennium()`: the `assignments` model and `registry_coverage`, the probe that rebuilds its spans, read it through that one helper so they agree on which spans stay open — and, since #135, which bienniums the span build reads at all: rows from any later biennium are dropped, so a pin to a past biennium rebuilds the record as of then (the publish shrink gate refuses that build). The PDC and SOS raw harvests also derive their early-capture year from it (`lookahead_election_year`: the pinned biennium's `start+1`, once held). Without it, each derives the biennium from the current UTC date (WA bienniums start on odd years). Useful for the rollover rehearsal and early-year edge cases.
- `USA_WA_PDC_APP_TOKEN` — **optional** Socrata application token for the PDC transport (#69; read by `usa_wa_adapter_pdc.raw_harvest` in the nightly `usa-wa-pipeline.service`), sent as the `X-App-Token` header only when set. Rate-limiting only (moves throttling from per-IP to per-app), **not** authentication — the dataset is public and readable without it, so it's not required at the once-daily single-GET volume. Register one free in a data.wa.gov profile to raise limits.
- `USA_WA_WSL_MIN_REQUEST_INTERVAL` — **optional** central courtesy floor (seconds) between any two WSL SOAP calls, across all `WSLClient` instances/services (#77). Default `0.5` (≤2 req/s); `0` disables. No surviving CLI takes `--pause-seconds` for WSL since #412 PR F deleted the historical harvests, so this variable is the only knob. Protects the single WSL upstream from bursts regardless of which caller is running.
- `USA_WA_SOS_MIN_REQUEST_INTERVAL` — **optional** central courtesy floor (seconds) between any two WA SOS **filings** calls (#100), the #77 pattern applied to `eledataweb.votewa.gov`. Default `1.0` (gentle — votewa is a low-QPS government site and the harvest is a handful of GETs); `0` disables. The production caller of `SOSFilingsClient` is `usa_wa_adapter_sos.raw_harvest`, which takes no `--pause-seconds`, so this variable governs every run.
- `USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL` — **optional** courtesy floor for the **results** source's host `results.vote.wa.gov` (#101) — the sibling of the above for the second SOS source (a *distinct* host, so its own limiter + knob). Read by `usa_wa_adapter_sos.raw_harvest`, which takes no `--pause-seconds`. Default `1.0`; `0` disables.
- `USA_WA_LEG_MIN_REQUEST_INTERVAL` — **optional** courtesy floor (seconds) between any two `leg.wa.gov` requests from the roster-PDF source (#236), the #77 pattern. `leg.wa.gov` is a *different* host from WSL's `wslwebservices.leg.wa.gov`, so its own limiter + knob rather than `USA_WA_WSL_MIN_REQUEST_INTERVAL`. Default `1.0`; `0` disables; `configure_leg_rate_limit()` maps the roster harvest's `--pause-seconds`, which defaults to `None` (#169 — unset leaves this variable in force). A run is one GET, three when a rotated media key forces href re-discovery; every request, redirect hops included, passes the limiter, and all but the first wait, so re-discovery adds two intervals (~2s at the default). In-process, like its siblings: it spaces requests within a run, and does not throttle repeated `--force` invocations.
- `USA_WA_RAW_ROOT` — root directory of the #302 raw-tier file store (#304): content-addressed wire objects + per-run manifests under `<root>/<source-slug>/`, written by the three nightly `raw_harvest` CLIs, the on-demand roster harvest (`roster_pdf.raw_harvest`, #421) and, since #412, the operator-attestation CLIs (`operators.cli`, `committees.succession_cli` — the raw store is their only provenance since #412 PR F), and verified by `python -m clearinghouse_core.raw_integrity` (weekly, `usa-wa-integrity-sweep`, since #412; a missing or empty store there exits 4). Default `raw/` relative to the invoker's cwd — set it to an absolute path (e.g. `/home/exedev/usa-wa/raw`) in `/etc/usa-wa/.env` (the harvest timers ship in `usa-wa-pipeline.timer`, #311), or a worktree run scatters stores per checkout.
- `USA_WA_OPERATOR` — **optional** name recorded as `entered_by` on an operator succession event or committee-succession link (`operators.cli`, `committees.succession_cli`); falls back to `$USER`.
- `USA_WA_PIPELINE_HERMETIC` — `1` lets the conformed crosswalk **and span** models build with no database (no registry crosswalk, no operator events) (set by `scripts/dbt-gate.sh` and the dbt tests only); any other state makes a missing `DATABASE_URL` fail the `dbt build` loudly (#302 CR). Never set it in `/etc/usa-wa/.env`.
- `USA_WA_PIPELINE_DB` — the dbt-duckdb database file the #302 pipeline builds into (#303, read by `packages/usa-wa-pipeline/dbt/profiles.yml`). Default `data/pipeline.duckdb` relative to the invoker's cwd; the commit gate (`scripts/dbt-gate.sh`) and the test suite always override it with a throwaway path.
- `USA_WA_DATASETS_ROOT` — root of the published-dataset tree (#311): the publisher writes immutable `<name>/<version>/` dirs + `catalog.json` here, and the API serves it at `/datasets/*` (resolved per request). Default `data/datasets` relative to the invoker's cwd / the service's WorkingDirectory — the primary checkout, so publisher and API agree without configuration.
- `USA_WA_BACKUP_BUCKET` — the GCS bucket the nightly backup writes and the restore reads (#434): `co-gcs-usa-wa-backup`. **No default** — unset is exit 2, since guessing a bucket is how bytes land where nobody reads. Set in `/etc/usa-wa/backup.env`, the one file `usa-wa-backup.service` loads; never in `/etc/usa-wa/.env`, which that unit does not read. [RECOVERY.md](RECOVERY.md)
- `USA_WA_BACKUP_PREFIX` — the database dumps' per-host prefix, `db/<prefix>/` (#434). Default: the hostname. The raw-store mirror takes none (content-addressed). The restore never defaults to it: `--latest` names the shipping host with `--prefix`.
- `USA_WA_BACKUP_CHECKIN_BASE_URL` — co-status's base URL for the backup's dead-man check-in (#455): `http://status:9000`, a tailnet name. `USA_WA_BACKUP_MONITOR_ID` — that monitor's id (a ULID; not the tenant's). Both in `/etc/usa-wa/backup.env`. **All three or none** with the key, which is never a variable: the unit's `checkin-key` credential (`/etc/usa-wa/backup-checkin.key`). None set → each run warns and checks in nothing; some → an error, and nothing. [RECOVERY.md](RECOVERY.md) § The dead-man monitor
- `GOOGLE_APPLICATION_CREDENTIALS` — the GCS key's path, read by the SDK. The backup unit sets it to its credential copy (`%d/gcs`) and refuses to start (exit 2) when `backup.env` overrides it; a restore run by hand passes `/etc/usa-wa/co-usa-wa-backup.json` (#434).
- `USA_WA_JOB_LEDGER` — `0` turns the #178 run ledger off for every `run_job` job, an explicit `ledger=True` included, and logs `job_ledger_disabled` (#135). Set only by the rollover rehearsal (`scripts/rollover-rehearsal.sh`), which runs the real nightly jobs against scratch roots — without it `/health/jobs` would serve a rehearsal run as its slug's latest. Any other value, unset included, records. **Never set in `/etc/usa-wa/.env`**: outside a rehearsal the nightly counts it as a failed stage (mailed) and keeps publishing.
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
- `PIPELINE_NIGHTLY_REHEARSAL` — a scratch dir: the #135 rollover rehearsal's mode.
  Every written path moves under it, and the registrar, serving load and coverage
  seed are skipped. The chain refuses to start unless `USA_WA_RAW_ROOT`,
  `USA_WA_PIPELINE_DB` and `USA_WA_DATASETS_ROOT` all lie inside it, none of them
  (nor the dir itself) lies in `$PWD` or the production checkout, and
  `USA_WA_JOB_LEDGER=0`. Set by `scripts/rollover-rehearsal.sh`; never by a unit.
- `PIPELINE_NIGHTLY_SKIP` — space-separated stage labels a **rehearsal** leaves out
  (the partial scenario skips the three harvests). Ignored outside a rehearsal.

### Rollover rehearsal — `scripts/rollover-rehearsal.sh`

Test seams, unset in real use ([RUNBOOK-ROLLOVER.md](RUNBOOK-ROLLOVER.md)):
`ROLLOVER_REHEARSAL_SOURCE` (the checkout whose `raw/` and `data/datasets/` are
copied; default `/home/exedev/usa-wa`), `ROLLOVER_REHEARSAL_ENV_FILES` (env files
read literally before the overrides; default `/etc/usa-wa/.env` and the source's
`.env`), `ROLLOVER_REHEARSAL_DIR` (the scratch dir, which must not exist yet;
default `~/rehearsal/<biennium>-<scenario>-<UTC stamp>`),
`ROLLOVER_REHEARSAL_BIENNIUM` (default `2027-28`) and `ROLLOVER_REHEARSAL_SYSTEMCTL`
(the `systemctl` asked whether `usa-wa-pipeline.service` is running; the wrapper refuses
while it is).

The PM sidecar's own tunables (`SidecarSettings` — `POWERMAP_BASE_URL`, `POWERMAP_API_KEY`, the drain/replay/reconcile cadences and the request-rate governor) were documented here until usa-wa#314 deleted the sidecar. Nothing reads them, and none is left in `/etc/usa-wa/.env` (checked 2026-10-06, #413).
