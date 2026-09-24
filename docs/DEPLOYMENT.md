# Deployment

Service topology, failure alerting, DB roles, the systemd lifecycle, and the
environment-variable reference for the single-VM deployment. Operating rules
that apply on nearly every task stay in [`AGENTS.md`](../AGENTS.md); this is the
detail behind them.
The host hazards — the main-only checkout guard (#87), shared-venv
integrity (#279), memory pressure (#389) and the disk GC (#394) — are in
[`DEPLOYMENT-HOST.md`](DEPLOYMENT-HOST.md).

## Services

| Service | Framework | Port | Managed by |
|---|---|---|---|
| API (live) | FastAPI | 8000 | `systemctl` (`usa-wa.service`) |
| WSL refresh (daily) | oneshot + timer | — | `systemctl` (`usa-wa-wsl-refresh.timer` → `.service`; 06:00 UTC). Pulls committees **and** the current-biennium meeting window for additive Joint/`Other` discovery (#39) |
| PDC archive refresh (daily) | oneshot, no timer | — | `systemctl` (`usa-wa-pdc-archive-refresh.service`, #201 Phase A). Archives the current biennium's winner cohorts (#121: both House generals + the three Senate cohorts), each SAVEPOINT-guarded, forced past the TTL. **No timer of its own** — pulled in by the rebuild unit below (`Wants=`) and ordered before it. Exit 4 = every cohort unserved |
| PDC refresh (daily) | oneshot + timer | — | `systemctl` (`usa-wa-pdc-refresh.timer` → `.service`; 06:30 UTC, #69; **identifier-only since #101, rebuild-only since #201**). Re-drives the builder off the archive → `person_wa_pdc` cross-links only (the House Position seat is the SOS refresh's since #101). Ordered after the WSL refresh (binds onto its House Persons + sponsor archive) and after its own archive half — `Wants=`, not `Requires=`: a Socrata outage alerts on the archive unit while this one re-derives from the last good archive |
| SOS archive refresh (daily) | oneshot, no timer | — | `systemctl` (`usa-wa-sos-archive-refresh.service`, #201 Phase A). Archives the current biennium's results cohorts — both the even seating year and the odd mid-biennium special (#106), each SAVEPOINT-guarded, forced past the TTL. **No timer of its own** — pulled in by the rebuild unit below. Exit 4 = every cohort unserved |
| SOS refresh (daily) | oneshot + timer | — | `systemctl` (`usa-wa-sos-refresh.timer` → `.service`; 06:45 UTC, #101; **rebuild-only since #201**). Re-drives the WSL+SOS House Position span builder off the archive → `usa_wa_legislature` `state_representative` Position seat **spans**. Ordered after the WSL refresh (reads its sponsor archive + binds its Persons) and after its own archive half (`Wants=`, not `Requires=` — see above); independent of the PDC refresh |
| Senate corroboration (daily) | oneshot + timer | — | `systemctl` (`usa-wa-senate-corroboration.timer` → `.service`; 07:00 UTC, #123). Consumes the odd-year `senate_winners()` cohort: field-cites each elected senator's open span `valid_from` to `sos-legresults:<odd>` (2a) + asserts no odd-year Senate winner lacks an open seat (2b — a silent missing operator `seated`); exit 1 → operator email. App-role DML (idempotent Citation insert). Ordered after the WSL + SOS refreshes rebuild the open Senate cohort + archive the odd results wire |
| House corroboration (daily) | oneshot + timer | — | `systemctl` (`usa-wa-house-corroboration.timer` → `.service`; 07:05 UTC, #149). The House sibling of Senate 2b — consumes the odd-year `house_winners()` cohort and asserts no odd-year House **special** winner `(LD, position)` lacks an open `state_representative` Position seat (the LD30/Hickel unseated-appointee shape a unit test can't catch); exit 1 → operator email. Read-only (no citation half — the House Position spans already cite the odd wire). `--sweep-biennia` is the #119 report-only historical audit. Ordered after the WSL + SOS refreshes rebuild the open House cohort + archive the odd results wire |
| Succession invariants (daily) | oneshot + timer | — | `systemctl` (`usa-wa-succession-invariants.timer` → `.service`; 07:15 UTC, #107). Read-only assertion of the open-seat cohort — 49 Senate / 98 House chamber counts + no duplicate occupancy; exit 1 → operator email (a missing operator succession event is otherwise silent). Ordered after the WSL/PDC/SOS refreshes rebuild the cohort |
| Committee lineage invariants (daily) | oneshot + timer | — | `systemctl` (`usa-wa-committee-lineage-invariants.timer` → `.service`; 07:30 UTC, #124 C4). Read-only coherence assertion — INV1 no `active=false` committee carries a live membership Assignment; INV2 the subject of a non-superseded `succeeded_by`/`merged_with` link is `active=false` (`split_from` exempt); exit 1 → operator email. Ordered after the refreshes + reconcile deactivate defunct committees + close their spans |
| Dataset pipeline (daily) | oneshot + timer | — | `systemctl` (`usa-wa-pipeline.timer` → `.service`; 08:00 UTC, #311). The #302 nightly chain: three raw harvests → dbt build → registrar → publish → serving load → parity probes (`scripts/pipeline-nightly.sh`). Harvest failures contained (last good wires + publish gates protect); a build failure aborts; registrar conflicts, a publish-gate refusal, or a parity divergence exit 1 → operator email while the last good catalog stands. Either exit restates each failed stage's last stdout line (its harness summary, counters included) as the run's closing lines, since the email carries only the last 25 (#331). Ordered after the canonical refreshes (they are the parity oracle) |
| Provenance integrity sweep (weekly) | oneshot + timer | — | `systemctl` (`usa-wa-integrity-sweep.timer` → `.service`; Sun 08:00 UTC) |
| Roster PDF edition re-check (monthly) | oneshot + timer | — | `systemctl` (`usa-wa-roster-pdf-recheck.timer` → `.service`; 1st 09:00 UTC, #237). The roster harvest (#225) run `--dry-run --force`: fetches the *Members of the Legislature* PDF, verifies its `Revision Date` against `DEFAULT_REVISION` in code, and rolls the archive write back. Exit 4 = a new edition is published (or the document cannot be located) → operator email, repeated monthly until the edition is harvested **and** the default bumped on `main` — runbook: [COMMANDS-ROSTER.md](COMMANDS-ROSTER.md) § Roster PDF. `--force` is load-bearing: the source's 90-day freshness cache would otherwise make every run a fetch-free cache hit. Not a refresh — nothing downstream reads it |
| Disk GC + free-space sensor (daily) | oneshot + timer | — | `systemctl` (`usa-wa-disk-gc.timer` → `.service`; 05:45 UTC, #394). `scripts/disk-gc.sh --prune`: reclaims tooling copies no running process references (VS Code server builds, Claude Code extension versions (#399), Claude plugin-cache versions no live session marks in use (#407), `_npx` trees), measures the repo tiers without touching them (#396 owns their retention), and exits 1 below the free-space floor → operator email. First in the chain, ahead of the 06:00 ingest — reclaim, then work. The one unit carrying **neither** `ExecStartPre` guard: it runs no repo code, and #87/#279 both fail in the worktree-heavy state that fills the disk |
| Failure alerts | templated oneshot | — | `OnFailure=` → `usa-wa-notify-failure@.service` |
| API (dev) | FastAPI | 8001 | manual uvicorn |

Every timer row above is pinned against the unit's own `OnCalendar=` by
`scripts/tests/test_docs_timer_drift.py` (#167): a shipped `deploy/*.timer` with
no row here — or a row whose cadence has drifted from the unit — fails the suite.
The same guard pins README's fresh-host provisioning block.

## Scheduled work outside systemd (#221)

One scheduled job does **not** run on this VM and so appears in neither table
above: `.github/workflows/context-cadence.yml`, a GitHub Actions workflow on
`11 18 * * 3` (Wed 18:11 UTC). It measures the agent-context surface —
`AGENTS.md` against its 6,000-token budget, plus the doc tree and cross-reference
seams — and appends **one** `baseline:scheduled` row to
`.skills/context-metrics.jsonl`, refreshing `.skills/context-token-ratio` and
`.skills/context-token-counts` in the same commit. It does not curate; curation
needs judgement and stays agent-triggered, prompted by what the rows show.
Installed and verified by the `curating-context` skill's `install-cadence.sh` —
re-run that rather than hand-editing the workflow.

Two consequences for anyone working on the VM:

- **`main` has a second author.** The job pushes as `github-actions[bot]`, so the
  prod checkout at `/home/exedev/usa-wa` falls behind `origin/main` roughly
  weekly. Nothing auto-pulls, and `assert-main-checkout.sh` asserts the branch
  *name*, not sync with origin — so nothing breaks, but **pull before you push**
  or a commit made on the VM's `main` hits a non-fast-forward.
- **Red means the mechanism broke, never that the surface grew.** Budget and seam
  drift are reported as `::warning::` and never fail the job. A failing run means
  the measurement did not happen — most likely the `ANTHROPIC_API_KEY` org secret
  stopped resolving, which the workflow preflights first precisely because the
  alternative failure is silence.

`.gitattributes` carries `.skills/context-metrics.jsonl merge=union` for this
job: the ledger is append-only, so a scheduled append racing a human commit lands
on the same last line and cannot auto-merge. The two calibration files it also
commits have no such driver — avoid landing a `curate context` commit inside the
Wed 18:11 UTC window.

## Failure alerting (#49)

The unattended oneshots fail silently on a headless box — a `failed` state in the
journal nobody is watching. Each failable oneshot (`usa-wa-migrate`,
`usa-wa-wsl-refresh`, `usa-wa-pdc-refresh`, `usa-wa-sos-refresh`,
`usa-wa-pdc-archive-refresh`, `usa-wa-sos-archive-refresh` (#201 — each half of a
daily cycle alerts on its own, so a source outage and a rebuild failure are
distinguishable from the email alone),
`usa-wa-integrity-sweep`,
`usa-wa-senate-corroboration`, `usa-wa-house-corroboration`,
`usa-wa-succession-invariants`,
`usa-wa-committee-lineage-invariants`, `usa-wa-pipeline` (#311), `usa-wa-disk-gc` (#394),
`usa-wa-roster-pdf-recheck` (#237 — its exit 4 is the new-edition notice)) carries
`OnFailure=usa-wa-notify-failure@%n.service`, so systemd starts the templated
handler on a non-zero exit **or** a `TimeoutStartSec=` hang. `%n` (the failing
unit's full name) becomes the handler's instance.

[`deploy/usa-wa-notify-failure@.service`](../deploy/usa-wa-notify-failure@.service)
runs [`scripts/notify-failure.sh`](../scripts/notify-failure.sh), which emails the
operator via the **exe.dev email gateway** (`POST
http://169.254.169.254/gateway/email/send`, a documented VM feature — no MTA/SMTP
creds needed). The reconcile exit-code contract (#44: 1 rejected / 2 auth / 3
guardrail abort) is surfaced **in the subject line** so a mass-retirement abort is
triageable without opening the journal. Recipient is `USA_WA_ALERT_EMAIL`
(`/etc/usa-wa/.env`); the script **fails closed** if it's unset — set it before
relying on alerts. The handler has no `OnFailure=` on itself (a failed send must
not recurse); a dropped alert still leaves the failure in the journal. The
serving unit (`usa-wa`) restarts in place via `Restart=` and so does not route
through this one-shot alert. The PM sync sidecar was the second such unit and
closed that gap itself (#85, in-process email on a failure streak); #314 deleted
it, so `Restart=` units are back to being covered by systemd state alone.

## DB role topology (defense-in-depth, issue #22)

DDL and DML rights are split across roles so a misconfigured DSN can't migrate/drop the live DB:

| Role | Rights | Used by |
|---|---|---|
| `usa_wa_owner` | owns all tables/sequences; CREATE/ALTER/DROP | `alembic upgrade head` only — the `usa-wa-migrate.service` oneshot |
| `usa_wa_app` | SELECT/INSERT/UPDATE/DELETE only (no DDL) | live API, WSL refresh timer, on-box CLIs |
| `usa_wa_test_owner` | owns the **separate** `usa_wa_test` database; DDL | `TEST_DATABASE_URL` — the suite owns its own schema lifecycle (`create_all`/drop per session) |

- `DATABASE_URL` (app role) serves; `DATABASE_URL_OWNER` (owner role, migrate host only) migrates. `alembic/env.py` prefers `DATABASE_URL_OWNER` when set, else `DATABASE_URL`.
- **`serving` is the one schema alembic does not own** (#313). It is a disposable projection of
  the published datasets, so `scripts/grants.sql` creates it and grants the app role `CREATE`
  *inside* it; the loader builds its own tables there and replaces every row each run. Drop the
  schema and the next `python -m usa_wa_api.serving.load` rebuilds it from `published/` alone.
  The app role deliberately cannot `CREATE SCHEMA` — Postgres checks that privilege before
  `IF NOT EXISTS` short-circuits, so the loader never issues one.
- [`scripts/grants.sql`](../scripts/grants.sql) is the version-controlled source of truth for grants — idempotent, re-applied after every migration by [`scripts/migrate.sh`](../scripts/migrate.sh). `ALTER DEFAULT PRIVILEGES` means new tables auto-grant DML to the app role. **Add new schemas to it** when a migration introduces one — and **remove one when a migration drops it**, in the same change. The two directions are not symmetric: a missing schema merely under-grants, while a `GRANT` naming a schema that no longer exists is an **error**, which would wedge `usa-wa-migrate` on the very deploy that dropped it. `sync` left the lists with #314 step C.
- Provision prod once as superuser: `psql -d usa_wa -v reassign_from=usa_wa -f scripts/grants.sql` (then per-role `ALTER ROLE … PASSWORD` out-of-band; passwords are never committed).
- The **test DB** needs only its role + ownership — do **not** run `grants.sql` against it (its schemas don't exist until the suite creates them, so the schema-grant steps would error). Provision with: `psql -c "CREATE ROLE usa_wa_test_owner LOGIN PASSWORD '…'"` then `ALTER DATABASE usa_wa_test OWNER TO usa_wa_test_owner`.
- The API lifespan logs a startup fingerprint (`current_user` + `current_database`) — role/DB confusion shows up in the first `journalctl` line.

## Deploy convention: units never sync the venv (issue #30)

**Deploy convention: units never sync the venv (issue #30).** Every systemd
entrypoint runs `uv run --frozen --no-sync` (`usa-wa.service`,
`usa-wa-wsl-refresh.service`,
`usa-wa-pdc-refresh.service`, `usa-wa-sos-refresh.service`,
`usa-wa-pdc-archive-refresh.service`, `usa-wa-sos-archive-refresh.service`,
`usa-wa-integrity-sweep.service`, `usa-wa-senate-corroboration.service`,
`usa-wa-house-corroboration.service`, `usa-wa-succession-invariants.service`,
`usa-wa-committee-lineage-invariants.service`, `usa-wa-roster-pdf-recheck.service`,
`scripts/migrate.sh`, `scripts/pipeline-nightly.sh`). The one exception is
`usa-wa-disk-gc.service`, which runs plain bash and no Python at all (#394).
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
sudo systemctl restart usa-wa
```

`uv sync` here uses `--locked` (not `--frozen`): it additionally asserts
`uv.lock` is consistent with `pyproject.toml`, catching a committed lock that
went stale — a deploy-time integrity check worth failing on. Units stay on
`--frozen` so a lock/pyproject drift can't wedge the daily timer.

If the venv is missing a locked dependency, units fail loudly at import — the
intended signal to run `uv sync`. **First provision (or after a venv wipe)
requires a plain `uv sync`** — `--no-sync` units can't start against an absent
`.venv`.

**Units are installed as copies, not symlinks.** Every `/etc/systemd/system/usa-wa*`
unit is a root-owned copy of its `deploy/` counterpart, so after editing a unit file
run `sudo cp deploy/<unit> /etc/systemd/system/` **before** the `daemon-reload` the
rows below prescribe — `daemon-reload` alone re-reads the stale installed copy and
silently deploys nothing.

## Lifecycle reference

| Situation | Action |
|---|---|
| Code committed to main | `sudo systemctl restart usa-wa` (run `uv sync --locked` first if `uv.lock` changed — units are `--no-sync`; see convention above) |
| Testing a worktree/branch | `uv run uvicorn ... --port 8001 --reload` |
| Debugging the live service | `sudo journalctl -u usa-wa -f` |
| After editing `deploy/usa-wa.service` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa` |
| After editing `deploy/usa-wa-wsl-refresh.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-wsl-refresh.timer` |
| After editing `deploy/usa-wa-pdc-refresh.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-pdc-refresh.timer` |
| After editing `deploy/usa-wa-sos-refresh.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-sos-refresh.timer` |
| After editing `deploy/usa-wa-{pdc,sos}-archive-refresh.service` | `sudo cp` + `sudo systemctl daemon-reload` (no timer and no `[Install]` — each is pulled in by its rebuild unit, so there is nothing to enable or restart) |
| After editing `deploy/usa-wa-integrity-sweep.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-integrity-sweep.timer` |
| After editing `deploy/usa-wa-senate-corroboration.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-senate-corroboration.timer` |
| After editing `deploy/usa-wa-house-corroboration.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-house-corroboration.timer` |
| After editing `deploy/usa-wa-succession-invariants.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-succession-invariants.timer` |
| After editing `deploy/usa-wa-committee-lineage-invariants.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-committee-lineage-invariants.timer` |
| After editing `deploy/usa-wa-roster-pdf-recheck.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-roster-pdf-recheck.timer` |
| After editing `deploy/usa-wa-disk-gc.{service,timer}` | `sudo systemctl daemon-reload && sudo systemctl restart usa-wa-disk-gc.timer` |
| After editing `deploy/usa-wa-notify-failure@.service` | `sudo systemctl daemon-reload` (templated `OnFailure=` handler — nothing to restart; next failure picks it up) |
| After DB model changes | `sudo systemctl restart usa-wa-migrate` (runs alembic + grants under the owner role), then restart usa-wa — run `uv sync --locked` first if `uv.lock` changed (`migrate.sh` is `--no-sync`). **`restart`, not `start`** — the unit is a `RemainAfterExit` oneshot, so once it's `active (exited)` from an earlier migrate this boot, `start` is a silent no-op (exits 0, applies nothing). |
| Run the WSL refresh now (ad-hoc) | `sudo systemctl start usa-wa-wsl-refresh.service` |
| Run the PDC refresh now (ad-hoc) | `sudo systemctl start usa-wa-pdc-refresh.service` (runs **both** halves — the `Wants=` pulls the archive unit in) |
| Run the SOS refresh now (ad-hoc) | `sudo systemctl start usa-wa-sos-refresh.service` (both halves, as above) |
| Refresh only a source's archive (ad-hoc) | `sudo systemctl start usa-wa-{pdc,sos}-archive-refresh.service` |
| Run the provenance integrity sweep now (ad-hoc) | `sudo systemctl start usa-wa-integrity-sweep.service` |
| Run the Senate corroboration now (ad-hoc) | `sudo systemctl start usa-wa-senate-corroboration.service` |
| Run the House corroboration now (ad-hoc) | `sudo systemctl start usa-wa-house-corroboration.service` |
| Run the succession invariant check now (ad-hoc) | `sudo systemctl start usa-wa-succession-invariants.service` |
| Run the committee lineage invariant check now (ad-hoc) | `sudo systemctl start usa-wa-committee-lineage-invariants.service` |
| Re-check the roster PDF edition now (ad-hoc) | `sudo systemctl start usa-wa-roster-pdf-recheck.service` (one 5.7MB GET; archives nothing) |
| Reclaim disk / check free space now (ad-hoc) | `sudo systemctl start usa-wa-disk-gc.service`, or `scripts/disk-gc.sh` for a report that removes nothing. Needs no DB and no venv — it is the one unit that still runs with a feature branch checked out |

## Validating unit edits (#51)

**Validating unit edits (#51).** A path-filtered pre-commit hook
(`systemd-verify-units` → [`scripts/verify-units.sh`](../scripts/verify-units.sh))
runs `systemd-analyze verify` on any changed `deploy/*.{service,timer}`. It
fails on a non-zero exit **and** on stderr warning markers (`Unknown key name`,
`Unknown section`, `ignoring`, …), because `systemd-analyze` exits 0 on
unknown/misspelled directives — a plain `$?` gate would pass them. Catches:
directive/section typos, malformed syntax, nonexistent `ExecStart=` binaries.
Does **not** catch misspelled `After=`/`Before=` ordering deps (systemd treats
ordering against absent units as legitimate) — that gap is closed instead by
[`scripts/tests/test_unit_ordering.py`](../scripts/tests/test_unit_ordering.py)
(#52), which asserts the intended `After=`/`Before=` graph as data and
cross-checks the on-disk unit set so a new unit forces an explicit ordering
decision. Neither notices a unit the **docs** never mention: that third gap is
[`scripts/tests/test_docs_timer_drift.py`](../scripts/tests/test_docs_timer_drift.py)
(#167), which pins the § Services cadences above and README's fresh-host enable
block against each timer's own `OnCalendar=`. Both tests read units through the
shared [`scripts/tests/systemd_units.py`](../scripts/tests/systemd_units.py)
parser, so they cannot disagree about what a unit says. No-ops where
`systemd-analyze` is absent. Because `verify` resolves absolute `ExecStart=` paths
(`/usr/local/bin/uv`) and `User=exedev` against the *local* box, off-VM it can
false-**fail** even with `systemd-analyze` present — a failure off-VM means "run
it on the VM," not "your unit is broken." Run ad-hoc:
`./scripts/verify-units.sh deploy/*.service deploy/*.timer`.
