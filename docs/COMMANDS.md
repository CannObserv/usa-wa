# Commands

Full command reference for `usa-wa`. The everyday subset is in [`AGENTS.md`](../AGENTS.md#common-commands); this file is the authoritative reference — full options, exit codes, and provenance/design rationale.

Grouped references split out of this file so each stays loadable on its own:

- [COMMANDS-SUCCESSION.md](COMMANDS-SUCCESSION.md) — operator succession events, odd-year corroboration, committee lineage
- [COMMANDS-SYNC.md](COMMANDS-SYNC.md) — Power Map reconcilers, heals, validation, provenance and integrity
- [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md) — historical harvests, span builders, one-shot migrations, write-free probes

## Command index

Every operational & backfill CLI — command + one-line purpose below, grouped by
the reference that documents its full options, exit codes, and design rationale.
Prod runs the daily/weekly ones on systemd timers (see
[`AGENTS.md`](../AGENTS.md#server-lifecycle) § Server Lifecycle); the rest are
run-once / ad-hoc. Pair backfills with `USA_WA_BIENNIUM` to target a non-current
biennium.

### Documented in this file

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.refresh` | Daily WSL pull — committees + meeting window + member cluster |
| `python -m usa_wa_adapter_pdc.refresh` | Daily PDC pull — House Position seats (#69) + Senate cross-links (#75) |
| `python -m usa_wa_adapter_pdc.harvest_pdc` | Historical PDC winner cohorts — archive-only, Phase A (#79) |
| `python -m usa_wa_adapter_pdc.build_pdc_spans` | Era-matched `person_wa_pdc` identifier links, Phase B (#79; identifier-only since #101) |
| `python -m usa_wa_adapter_pdc.migrate_pdc_spans` | Retire pre-#79 per-biennium PDC House rows onto spans (#79) |
| `python -m usa_wa_adapter_sos.results.harvest` | Archive WA SOS **results** cohorts (the House Position source, `usa_wa_sos_results`) — Phase A (#101) |
| `python -m usa_wa_adapter_sos.house.build` | WSL+SOS House Position seat spans (2008→present) incl. #103 elimination inference, Phase B (#101) |
| `python -m usa_wa_adapter_sos.house.migrate` | Superseded-collapse (#103) + re-source usa_wa_pdc House rows → usa_wa_legislature (owner role, #101) |

### Succession, corroboration, and committee lineage

Full options, exit codes and rationale: [COMMANDS-SUCCESSION.md](COMMANDS-SUCCESSION.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_sync_powermap.reconcile_committee_active` | Reconcile PM `active` vs current roster (#44; weekly) |
| `python -m usa_wa_adapter_legislature.operator_events` | Record operator succession events — the live interjection surface (#107) |
| `python -m usa_wa_adapter_legislature.succession_invariants` | Assert chamber counts + seat occupancy; exit 1 on drift (#107; daily) |
| `python -m usa_wa_adapter_sos.senate_corroboration` | Cite elected senators + assert no odd-year Senate winner lacks an open seat; exit 1 on drift (#123; daily) |
| `python -m usa_wa_adapter_sos.house_corroboration` | Assert no odd-year House special winner lacks an open Position seat; `--sweep-biennia` historical audit; exit 1 on drift (#149; daily) |
| `python -m usa_wa_adapter_legislature.committee_succession` | Record operator committee-succession links — the judgment layer (#124 C2) |
| `python -m usa_wa_sync_powermap.committee_event_producer` | Emit committee lifecycle windows + succession links to PM as org events (#124 C3) |
| `python -m usa_wa_adapter_legislature.committee_lineage_invariants` | Assert committee lineage coherence (INV1/INV2); exit 1 on drift (#124 C4; daily) |
| `python -m usa_wa_adapter_legislature.committee_lineage_suggest` | Advisory: rank committee succession-candidate pairs (#124 C5) |

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
| `python -m usa_wa_sync_powermap.prune_subscriptions` | Unsubscribe PM-only strangers; re-run to stale=0 (#73) |
| `python -m usa_wa_sync_powermap.retract_assignments` | Retract spurious anchored assignments on PM (`op:"retract"`) + tombstone locally; sidecar-paused (#144 Phase 2) |
| `python -m clearinghouse_core.integrity` | Provenance integrity sweep — rolling byte-slice (#54/#55; weekly) |
| `python -m usa_wa_adapter_legislature.baseline_unbaselined_committees` | OWNER-role provenance repair (#64) |

### Historical backfill and probes

Full options, exit codes and rationale: [COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md).

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_legislature.probe_committee_extent` | Write-free: how much committee history exists (#64) |
| `python -m usa_wa_adapter_legislature.probe_member_identity [--history]` | Write-free: is the WSL member Id stable (#27/#81) |
| `python -m usa_wa_adapter_legislature.harvest_committee_meetings` | Joint/Other backfill + seed freeze (#39) |
| `python -m usa_wa_adapter_legislature.ingest_committee_seed` | No-WSL Joint/Other seed loader (#39) |
| `python -m usa_wa_adapter_legislature.harvest_sponsors` | Historical member backfill — Persons only, Phase A (#77) |
| `python -m usa_wa_adapter_legislature.harvest_sponsor_spans` | Merged-span member Assignments, Phase B (#78) |
| `python -m usa_wa_adapter_legislature.migrate_sponsor_spans` | Collapse stranded party/Senate rows (3-part legacy #78-3 + superseded 4-part #97) onto merged spans (owner role) |
| `python -m usa_wa_adapter_legislature.harvest_committee_members` | Historical committee rosters — Persons only, Phase A (#82) |
| `python -m usa_wa_adapter_legislature.harvest_committee_member_spans` | Merged committee-membership spans, Phase B (#82) |
| `python -m usa_wa_adapter_legislature.migrate_committee_spans` | Retire per-biennium committee rows stranded by deeper spans (#82) |
| `python -m usa_wa_adapter_legislature.migrate_member_role_types` | Reclassify generic `member` Roles → PM catalog slugs (`committee_member`/`party_member`) to stop the #110 no-op-gate churn |
| `python -m usa_wa_adapter_legislature.harvest_committees` | Committee historical backfill, Phase A (sub-project 3) |
| `python -m usa_wa_sync_powermap.reconcile_committee_name_chain` | Full committee rename-chain emit, Phase B (sub-project 3) |

### Not yet documented

Indexed here but carrying no reference section yet — see [#166](https://github.com/CannObserv/usa-wa/issues/166).

| Command | Purpose |
|---|---|
| `python -m usa_wa_adapter_sos.filings.harvest` | Archive WA SOS votewa **filing** cohorts (candidacy metadata, `usa_wa_sos`) — Phase A (#100) |

## Setup

```bash
# Install dependencies (creates .venv, locks deps in uv.lock)
uv sync

# Install pre-commit hook (runs ruff on commit)
uv run pre-commit install
```

## Environment

Production secrets live in `/etc/usa-wa/.env`; dev/agent secrets in `./.env`. Both are git-ignored. The systemd unit loads them automatically; shell sessions must load manually:

```bash
export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)
```

## Dev server

```bash
# Port 8001 — port 8000 belongs to systemd, never start uvicorn there manually
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload --log-config packages/usa-wa-api/src/usa_wa_api/log_config.json
```

Reachable at `https://usa-wa.exe.xyz:8001/` via the exe.dev proxy.

## Tests

```bash
# Full suite — requires TEST_DATABASE_URL set to a non-prod database
uv run pytest

# Single file (skip the coverage gate, which measures all of packages/)
uv run pytest --no-cov packages/usa-wa-api/tests/test_health.py

# Integration-marked tests only (excluded by default)
uv run pytest -m integration
```

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
# WSL refresh — one-shot pull from CommitteeService.GetActiveCommittees, plus an
# additive current-biennium meeting-window pull for Joint/Other discovery (#39).
# Prod runs this daily at 06:00 UTC via the usa-wa-wsl-refresh.timer systemd
# unit; the command below is the manual / backfill form (pair with USA_WA_BIENNIUM).
# Also drives the member cluster: forced GetSponsors + a per-committee
# GetCommitteeMembers(current, ...) fan-out (#82), then re-drives BOTH span builders for the
# current cohort — party/Senate-seat (#78-2c) and committee membership (#82). fill_only
# (#65 — additive, never clobbers PM-curated rows).
python -m usa_wa_adapter_legislature.refresh

# PDC refresh (#69 + #75; IDENTIFIER-ONLY since #101) — emits the person_wa_pdc cross-source
# identifier links (House winners + #74 movers + #75 Senate), archive-first from the PDC Campaign
# Finance Summary Socrata dataset (3h9x-7bvm) on data.wa.gov. Archives every winner cohort the
# current biennium's membership can be decided by (#121): both House generals (even seating + odd
# mid-biennium special) and the three senate-winners:<Y> cohorts (staggered evens + the odd
# special) via archive_only — each in its own SAVEPOINT (a transient Socrata failure skips one
# cohort, not the daily unit; a raceless year returns an empty row set, a success) — then
# re-drives build_pdc_spans scoped to the current biennium for the links. The House Position SEAT
# is no longer PDC's — it is the WSL+SOS builder's (usa-wa-sos-refresh, below),
# usa_wa_legislature-sourced and symmetric with the Senate seat (#101). Prod runs this daily at
# 06:30 UTC (after the WSL refresh) via usa-wa-pdc-refresh.timer; the form below is the manual
# surface. USA_WA_PDC_APP_TOKEN (optional).
python -m usa_wa_adapter_pdc.refresh

# SOS refresh (#101) — the daily driver of the WSL+SOS House state_representative Position seat.
# Archives the current election's results cohort (sos-legresults:<YYYYMMDD>) via archive_only,
# then re-drives build_house_position_spans scoped to the current biennium -> usa_wa_legislature
# Position seat spans (current biennium = the open end). Reads the sitting roster archive-first from
# the WSL sponsor archive (who sits) + the SOS archive (the Position). Prod runs this daily at 06:45
# UTC (after the WSL refresh) via usa-wa-sos-refresh.timer; independent of the PDC refresh.
python -m usa_wa_adapter_sos.house.refresh
```

### PDC historical backfill (#79)

```bash
# The #75 fix: each PDC election cohort must match the roster of the biennium it SEATED, not the
# current one. Era-scoped historical backfill of House Position seat spans + person_wa_pdc links.
# DEPENDS ON #77 (Persons + the sponsor archive) — a pre-#77 winner's Person is absent so its span
# is skipped (logged, correct); run this after the sponsor harvest.

# Phase A — archive the winner cohorts (archive-only; no normalize). EVERY general-election year
# (#121 — odd-year specials seat legislators; Nov 2025: Hunt/Krishnadasan/Zahn) from the floor
# (2008) to the current calendar year (the default --to-year); a year with no data archives an
# empty cohort (negative evidence, no error path); cache-hit on re-run. A mid-sweep failure aborts
# the run (nothing committed) — re-run from the floor (closed years cache-hit).
# --pause-seconds drips between years (SODA analog of the WSL harvests' pacing).
python -m usa_wa_adapter_pdc.harvest_pdc --dry-run
python -m usa_wa_adapter_pdc.harvest_pdc --from-year 2008 --pause-seconds 0.5

# Phase B — era-matched IDENTIFIER build (archive-first, no live PDC pull; identifier-only since
# #101): each cohort pairs with its seating biennium's sponsor roster — an even year seats the
# NEXT biennium (2012 → 2013-14), an odd special seats the biennium STARTING that year (2025 →
# 2025-26, #121) — matches each winner to a WSL Person, emits person_wa_pdc links. A cohort
# seating a FUTURE biennium (the just-run November even general, archived Nov-Dec) is skipped +
# logged (pdc_cohort_future_biennium_skipped) until its roster exists — the next cycle links it
# (#121 CR; the rollover-readiness audit is #135). The House Position SEAT is no longer built
# here (that is usa_wa_adapter_sos.house.build, below). Idempotent.
python -m usa_wa_adapter_pdc.build_pdc_spans --dry-run
python -m usa_wa_adapter_pdc.build_pdc_spans

# Migration — OWNER ROLE, run AFTER build_pdc_spans, sidecar paused. Retires the pre-#79
# per-biennium usa_wa_pdc House rows ({member}:chamber-house:{biennium}, 3-part) stranded by the
# 4-part span key: maps each to the covering span by (person, role) + window, transfers the PM
# anchor, hard-deletes the row + its citations (owner-only under #54). A row with no covering span
# yet is left as orphans_no_span (re-run after the build). anchors_dropped (>0) = the sidecar
# anchored the span first, orphaning the legacy PM assignment (the #80 start-date gap).
python -m usa_wa_adapter_pdc.migrate_pdc_spans --dry-run
python -m usa_wa_adapter_pdc.migrate_pdc_spans
```

### WSL+SOS House Position backfill (#101)

```bash
# The re-partition (#101): the WA House state_representative Position seat is now
# usa_wa_legislature-sourced (symmetric with the Senate seat, #75). WSL drives membership (who
# sits, the sponsor roster); WA SOS results.vote.wa.gov drives the ballot Position 1/2 (back to
# 2008); PDC is demoted to the person_wa_pdc identifier link. ONE builder drives both the daily
# re-drive (usa-wa-sos-refresh) and this historical backfill, so a member serving ACROSS the 2018
# boundary builds the same deep span either way — the #100 CR finding-1 depth mismatch cannot recur.
# Coverage: Position 2008->present (the results floor); pre-2008 stays honestly position-less.

# Phase A — archive the results.vote.wa.gov legislative cohorts (archive-only; CSV wire hashed #54,
# discovered via each election's export.html). EVERY general-election year from the floor (2008) to
# current (#106: odd years too — a WA general runs each November and an odd-year special seats
# legislators, e.g. Hunt LD5 Senate Nov 2025; default --to-year = current calendar year); closed
# years cache-hit on re-run; pacing via --pause-seconds. PER-YEAR RESILIENT: an HTTP 404/500 year is
# `skipped` (only this raises the whole-source outage warning), a no-legislative-race year with no
# CSV (2021/2023) is the expected `no_legislative_race`, each rolled back to its SAVEPOINT while the
# reached years commit.
python -m usa_wa_adapter_sos.results.harvest --dry-run
python -m usa_wa_adapter_sos.results.harvest --from-year 2008 --pause-seconds 1.0

# Phase B — WSL+SOS House Position span build (archive-first, no live pull): the sitting House
# roster (WSL sponsor archive) x the SOS results archive (the Position) -> merged usa_wa_legislature
# state_representative Position seat spans, cite-every-biennium onto sos-legresults:<Y>. A sitting
# member with no resolvable SOS position gets no seat (OQ1: emit nothing, counted missing_position)
# — UNLESS within-LD elimination (#103) resolves it: an LD with exactly 2 sitting members, exactly
# 1 ballot-claimed seat, and exactly 1 unmatched member gives that member the remaining position
# (a mid-biennium appointee, or a ballot<->roster name change). Inferred (member, biennium) pairs
# cite the WSL sponsor roster (the wire that names them), are logged (house_seat_inferred), and
# surface as coverage["inferred"]. DEPENDS ON Phase A + the WSL sponsor archive/Persons (#77).
# Ends with the #83 stale-span sweep (usa_wa_legislature, chamber-house); same mass-close guard
# (--max-close-fraction, (0,1], 1.0 disables). --biennium scopes to a biennium's current members
# (each keeps full history). ROSTER HYGIENE (#105): each biennium's roster sheds (a) mover rows
# — a House row whose Id also appears in a named Senate row of the same wire (Alvarado/Hunt;
# house_roster_mover_excluded) — and (b) committee-corroborated stale rows — a named member
# absent from that biennium's committee-roster archive (Senn/Kilduff ghosts;
# sponsor_stale_row_excluded), guarded by --stale-min-coverage (default 0.9: a biennium whose
# committee cohort names <90% of the wire's named members skips the exclusion —
# stale_exclusion_skipped_low_coverage — so a thin archive never reads as mass departure; >1
# disables entirely) AND by the tail rule (excluded only when committee-absent in that biennium
# and every later one — later presence = archive gap, rescued:
# stale_exclusion_rescued_by_later_presence). Both un-block the #103 elimination (the LD reads 2-member again) and drop
# the ghost's seat assertion so the sweep closes it. Audit historically: --dry-run + read the
# exclusion log lines before an unrestricted rebuild.
# PRE-2009 BACK-CHAIN (#118 Phase 1): the SOS ballot floors at the 2008 general, so a pre-2009 House
# member has no ballot to position. A WA rep holds a specific Position continuously, so this walks
# the archived biennia newest->oldest and carries each ballot-anchored Position back through
# uninterrupted same-LD tenure (the direct seed), letting the #103 elimination resolve the mate each
# biennium (the 1-hop cascade). Reaches the 2001-map era pre-2009 biennia (2003-04..2007-08); the
# 1991-2001 era has no reachable ballot anchor (#140). Back-chained seats cite the sponsor roster,
# log house_seat_backchained (with max hop depth), and surface as coverage["seeded"]. Guardrails: a
# redistricting era break (1993-94/2003-04/2013-14/2023-24 — WA keeps LD numbers, so the break is
# explicit) and an LD move / tenure gap both stop the chain; --max-backchain-hops caps the depth
# (default 4; 0 disables). Runs in BOTH the daily re-drive and this backfill (idempotent), so span
# identity holds. Only ballot-class positions carry back — an elimination-only mate does not seed its
# own earlier tenure (that recursive cascade is Phase 2, deferred).
python -m usa_wa_adapter_sos.house.build --dry-run
python -m usa_wa_adapter_sos.house.build

# Migration — OWNER ROLE, one-shot, run AFTER usa_wa_adapter_sos.house.build. TWO passes:
# (1) #103 within-source superseded collapse FIRST — elimination deepens some tenures, so an
# existing anchored usa_wa_legislature row can be superseded by a new deeper-start row of the same
# seat (the #97 sponsor pattern); each collapses onto its earlier-start covering keeper
# (superseded_retired), transferring the anchor — a keeper that merged in place already carries its
# own anchor, so the superseded one is dropped + warned (one PM assignment orphaned upstream, #80).
# (2) The #101 PDC re-source collapse: retires existing usa_wa_pdc 4-part chamber-house rows onto
# the SURVIVING usa_wa_legislature span that COVERS them (mapped by (person, role) + validity
# window — NOT exact source_id: PDC omits the pre-2018 position, so a cross-2018 incumbent's
# existing PDC span is shallow …:2019-20 while the SOS builder emits a deeper …:2017-18). Transfers
# the PM anchor (PM keys on (person, role, start), so the deep keeper IS that tenure), deletes the
# retired row + its citations (owner-only #54). A PDC row with no covering keeper is left as
# orphans_no_keeper. 3-part legacy rows are migrate_pdc_spans's job (skipped_legacy).
# Idempotent; --dry-run.
python -m usa_wa_adapter_sos.house.migrate --dry-run
python -m usa_wa_adapter_sos.house.migrate

# DEPLOY SEQUENCING (the whole historical backfill — and any build that changes span depth, e.g.
# enabling #103 elimination), SIDECAR PAUSED throughout, completed before the next 06:45 SOS timer
# fire. Order matters: build BEFORE migrate, so the deep usa_wa_legislature keeper spans exist for
# the migration to collapse the stranded PDC + superseded rows onto (transferring their anchors) —
# before anything drains to PM. Draining first would let PM dedup-match a new span onto a
# still-anchored old row's assignment ((person, role, start_date)) and park the entry UNAVAILABLE
# (#86 anchor conflict + operator alert).
#   sudo systemctl stop usa-wa-sync-powermap
#   python -m usa_wa_adapter_sos.results.harvest --from-year 2008        # Phase A (SOS results archive)
#   python -m usa_wa_adapter_sos.house.build                   # Phase B: full-depth rebuild
#   python -m usa_wa_adapter_sos.house.migrate                # OWNER role: superseded + PDC->WSL
#   sudo systemctl start usa-wa-sync-powermap                        # let the sidecar drain to PM
# If the 06:45 timer beats this window: the daily build emits the new spans first and the sidecar
# parks the colliding entries UNAVAILABLE (#86 anchor conflict, operator alert) — expected and
# recoverable: run the migrate, then redrive (python -m usa_wa_api.cli.redrive).
```

## Submodules

The `skills-vendor/` directory holds upstream skill repos as submodules. They are updated automatically by the `UserPromptSubmit` hook in [`.claude/settings.json`](../.claude/settings.json), but the manual commands are:

```bash
# Initialize submodules on a fresh clone
git submodule update --init --recursive

# Update vendored skills to the latest upstream main
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
```
