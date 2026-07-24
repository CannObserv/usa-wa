# Commands

Full command reference for `usa-wa`. The everyday subset is in [`AGENTS.md`](../AGENTS.md#common-commands); this file is the authoritative reference — full options, exit codes, and provenance/design rationale.

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
uv run uvicorn usa_wa_api.api.main:app --host 0.0.0.0 --port 8001 --reload
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

See AGENTS.md § Server Lifecycle for the full unit-by-unit restart matrix and the
`--no-sync` / `uv sync --locked` deploy convention.

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
# Finance Summary Socrata dataset (3h9x-7bvm) on data.wa.gov. Archives the current biennium's winner
# cohorts (house-winners:<Y> + both staggered senate-winners:<Y>) via archive_only, then re-drives
# build_pdc_spans scoped to the current biennium for the links. The House Position SEAT is no longer
# PDC's — it is the WSL+SOS builder's (usa-wa-sos-refresh, below), usa_wa_legislature-sourced and
# symmetric with the Senate seat (#101). Prod runs this daily at 06:30 UTC (after the WSL refresh)
# via usa-wa-pdc-refresh.timer; the form below is the manual surface. USA_WA_PDC_APP_TOKEN (optional).
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

# Phase A — archive the winner cohorts (archive-only; no normalize). Even election years from the
# floor (2008) to current; a year with no data archives empty; cache-hit on re-run. A mid-sweep
# failure aborts the run (nothing committed) — re-run from the floor (closed years cache-hit).
# --pause-seconds drips between years (SODA analog of the WSL harvests' pacing).
python -m usa_wa_adapter_pdc.harvest_pdc --dry-run
python -m usa_wa_adapter_pdc.harvest_pdc --from-year 2008 --pause-seconds 0.5

# Phase B — era-matched IDENTIFIER build (archive-first, no live PDC pull; identifier-only since
# #101): each cohort pairs with its seating biennium's sponsor roster (2012 → 2013-14), matches each
# winner to a WSL Person, emits person_wa_pdc links. The House Position SEAT is no longer built here
# (that is usa_wa_adapter_sos.house.build, below). Idempotent.
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

## Operator succession (#107)

Mid-biennium successions (death, resignation, appointment) are invisible to every
wire signal — the cumulative WSL wire keeps a departed member named + committee-listed,
so their tenure span stays ghost-open, and an appointee's span starts at the biennium
floor, not the appointment date. Operators know these facts (news-first) and **interject**
them as `OperatorEvent`s. Each event is applied as an authoritative **overlay** by all
three span builders (sponsor / SOS-house / committee) after `build_tenure_spans`, before
emit; the daily refreshes re-drive the builders, so the overlay re-applies every run and
the wire can never win back a corrected span (self-durable). Provenance is first-class:
each write appends a hashed `FetchEvent` + `RawPayload` under the `usa_wa_operator` Source
(integrity-sweep covered, #54) and the touched span carries a field-level `Citation`.

```bash
# Record operator succession events (#107) — the live interjection surface. Three kinds
# split by scope so a chamber move never touches the party span:
#   departed (person-scoped, no seat) — the member stops serving entirely; every open span
#     (seat + party + committee) closes at the date. Death, full resignation, expulsion.
#   vacated  (seat-scoped) — ONE named seat's span closes at the date; party + committees
#     untouched. A chamber move's old seat, or a single-seat resignation.
#   seated   (seat-scoped) — one named seat's span opens at the date (instead of the
#     biennium floor), synthesized if the wire built none. Appointment, swearing-in.
# A chamber move = vacated(old seat) + seated(new seat) on the same member, each applied by
# the builder that owns that seat kind. seat_kind/seat_discriminator name the seat the same
# way the builders key it: chamber-senate + LD, chamber-house + ld-{n}-position-{p},
# committee + the WSL committee id. Validates kind/reason/seat shape AND that member_id
# resolves to a usa_wa_legislature Person (a typo would be a silent no-op overlay).
# App-role DML (writes operator_events + provenance); shell access is the trust boundary,
# as with the redrive CLI. Provenance is append-only — a date-correction is --supersede
# (a NEW row stamping the prior one's superseded_by_id), never a mutation (#54).
# --dry-run validates + writes, then rolls back. Exit 2 on a validation failure.
python -m usa_wa_adapter_legislature.operator_events \
    --member-id 29091 --kind departed --reason died \
    --effective-date 2025-04-19 --evidence-url https://... --dry-run
python -m usa_wa_adapter_legislature.operator_events \
    --member-id 35410 --kind seated --reason appointed \
    --seat-kind chamber-senate --seat-discriminator 5 \
    --effective-date 2025-06-03 --evidence-url https://...
python -m usa_wa_adapter_legislature.operator_events --file events.json   # JSON-array batch
python -m usa_wa_adapter_legislature.operator_events --supersede <id> \
    --member-id 35410 --kind seated --reason appointed \
    --seat-kind chamber-senate --seat-discriminator 5 \
    --effective-date 2025-06-10 --evidence-url https://...   # date-correction of <id>
python -m usa_wa_adapter_legislature.operator_events --list               # current events

# Succession invariant check (#107) — read-only anti-drift backstop + the #107 acceptance
# oracle. A MISSING operator event is silent (a member dies, nobody records it → a ghost-open
# span inflates the chamber for up to a biennium); this oneshot makes that loud. Against the
# live open-seat cohort it asserts:
#   chamber-count — open state_senator == 49, open state_representative == 98 (147 total).
#     High (50/99) ⇒ a ghost-open predecessor (a missing departed/vacated); low (48/97) ⇒
#     an over-closed / unfilled seat (a missing seated).
#   duplicate-occupancy — no seat Role with two open occupants, and no member holding two
#     open seats in the same chamber (the "two open senators in LD5" shape).
# Read-only (app role, no writes). Exit 0 clean / 1 on any violation (the offending
# seats/members named in the succession_invariants_violation log line) — the exit 1 is what
# the OnFailure=usa-wa-notify-failure@ handler emails the operator on. Prod runs this daily
# at 07:15 UTC via usa-wa-succession-invariants.timer, AFTER the WSL 06:00 / PDC 06:30 /
# SOS 06:45 refreshes rebuild the current-biennium cohort. --expected-senate/--expected-house
# override the WA chamber constants for a redistricting count change.
python -m usa_wa_adapter_legislature.succession_invariants
```

## Reconcilers & validation (PM sync)

Emit-only producer CLIs (PM stays the authority; they mirror curation back) plus
read-only validation. Weekly timers in prod; the forms below are the manual /
dry-run surface. No operator token — shell access is the trust boundary.

```bash
# Contact-label backfill (#31) — re-observation of produced orgs holding a phone,
# so PM adopts the synthesized contact display_label. Idempotent + re-runnable;
# --dry-run counts the cohort without submitting. Since #34 the sidecar self-heals
# carry-field drift on its own (anchored-cohort reconcile re-enqueues an ENRICH on a
# local-fingerprint mismatch), so this is now a force-push convenience, not the only
# recovery path.
python -m usa_wa_sync_powermap.backfill_contact_labels --dry-run
python -m usa_wa_sync_powermap.backfill_contact_labels

# Committee active-flag reconciliation (#44) — reconciles PM `active` for WSL committees
# against the current biennium's `GetCommittees(biennium)` roster: `active=false` for the
# absent, `active=true` for the returning (reactivation self-heals a transient partial-pull
# false retirement next cycle). Explicit-membership diff (not current-only
# GetActiveCommittees), guarded by an empty-pull abort + a cohort floor (--max-absent-fraction,
# default 0.34) so a partial WSL pull can't mass-retire. Skips archived/deleted/unanchored;
# emit-only (PM mirrors `active` back). Idempotent.
# Live-era scoping (#90): the diff is restricted to committees whose WSL Id appears in the
# current OR immediately-prior biennium roster (present_ids ∪ prior_ids; the prior roster's
# raw Ids read archive-first via CommitteeRosterCohortProvider). The historical committee
# backfill (harvest_committees, model A) added ~152 defunct-era committee orgs, all defaulting
# active=true; absent from the current roster they'd read as a mass retirement and trip the
# floor every run. Scoping drops them before the diff (counted `scoped_out`) while a genuine
# prior-biennium retirement (in prior, gone from current) still fires. Retirement window is
# one biennium — a multi-biennium reconcile outage strands a vanished committee active=true.
# Prod runs this weekly (Sun 07:00 UTC) via usa-wa-reconcile-committee-active.timer (#48).
# --dry-run previews the diff. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_active --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_active --biennium 2025-26

# Committee rename detection (#46) — write-side sibling of #45's read mirror. Diffs
# `GetCommittees(current)` vs `GetCommittees(prior)` on the stable `Id`; a changed
# `normalize_name(LongName)` is a rename. Emits windowed dated-name evidence (prior name
# typed `former`, effective_end = biennium-start boundary; new name typed `legal`,
# effective_start = same, open end — #58) so PM curates is_canonical and the #45 read mirror
# brings the windows back — emit-only, no local write. Diffs WSL's RAW LongName, not the
# PM-resolved Organization.name scalar (which would false-fire on PM canonicalisation + miss
# round-tripped renames). Guarded by empty-pull (either roster) + low-overlap
# (--min-overlap-fraction, default 0.5; stable WSL Ids → a real diff overlaps heavily, so a
# thin overlap = wrong-biennium pull) + rename-storm floor (--max-rename-fraction, default
# 0.34). Skips unanchored + live-cohort-absent (hidden vs unproduced). Idempotent.
# Prod runs this weekly (Sun 07:30 UTC) via usa-wa-reconcile-committee-names.timer (#53),
# staggered 30 min off the active reconcile.
# --dry-run previews. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_names --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_names --biennium 2025-26

# Joint/Other rename detection (#56) — meeting-derived sibling of #46 for the org_type='other'
# class CommitteeService can't see (#39; e.g. ESEC Id 13945). Diffs two bienniums'
# GetCommitteeMeetings-derived cohorts (current + prior) on the stable `Id`; the cohort name
# is the CLEAN `Name` (#61 observed_name), not the double-prefixed LongName stored as
# Organization.name — so the "Joint Joint …" form never reaches PM. Same windowed emit +
# shared spine as #46, but re-tuned guards for a dormancy-prone cohort: low-overlap OFF by
# default (--min-overlap-fraction 0.0 — window-absence is dormancy, not a wrong-biennium
# signal) and the storm fraction only weighed past --storm-floor-min-overlap (default 5).
# Window-absence ≠ rename (intersects ids present in BOTH windows). Emit-only; idempotent.
# Archive-first + read-only: a closed window is re-parsed offline from the RawPayload the daily
# refresh / #39 harvest already archived (no ~1.5MB re-pull); only an un-archived window falls
# back to a live, un-archived pull. Prod runs this weekly (Sun 07:45 UTC) via
# usa-wa-reconcile-committee-meeting-names.timer, staggered 15 min off #46.
# --dry-run previews. Biennium: --biennium, else USA_WA_BIENNIUM, else current date.
# NOTE backfill: the detector diffs current-vs-PRIOR biennium, so an older rename (ESEC =
# 2023) needs a targeted --biennium 2023-24 (diffs vs 2021-22) to surface.
# Exit codes: 0 clean; 1 some rows rejected/failed; 2 auth block; 3 guardrail abort.
python -m usa_wa_sync_powermap.reconcile_committee_meeting_names --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_meeting_names --biennium 2023-24

# Committee ↔ PM validation (#64) — read-only. For each PM-linked produced org, diff local
# canonical state against PM's live OrgDetail and bucket discrepancies (name/acronym/window/
# parent drift, unlinked/missing/merged), splitting reconciled (PM curation roundtripped)
# from divergent. Emit-nothing; sequential reads + bounded backoff.
# Exit 0 clean / 1 divergent / 2 auth / 3 empty-cohort abort.
python -m usa_wa_sync_powermap.validate_committees          # human table
python -m usa_wa_sync_powermap.validate_committees --json   # machine-readable

# Force-adopt PM curation for LWW-locked committees (#65 Part 2) — one-shot heal. For the
# anchored produced cohort, re-fetch each PM OrgDetail and force-apply it (upsert_from_pm +
# clock-parity stamp), bypassing LWW. Idempotent (no-op at parity). App-role local write.
# Remedies TWO skew sources: the pre-fill-only refresh (#65) and the anchor-stamp bump
# (#109) — pre-fix, every created row landed ahead of PM by the delivery round-trip, so
# reach for this after an anchor-stamp-era org skew as well (org is deliberately ungated,
# so nothing else converges it). Reported counter is force-adopts attempted, NOT rows
# changed.
python -m usa_wa_sync_powermap.heal_committee_curation --dry-run
python -m usa_wa_sync_powermap.heal_committee_curation

# Heal LWW-skewed assignment clocks (#102) — one-shot, the assignment analog of the committee
# heal above. A 2026-07-06 span backfill bumped ~4,300 anchored assignments' local updated_at
# ahead of PM, so the reconcile re-POSTs an IDENTICAL observation every cycle forever (PM no-ops
# it without advancing its clock → 429s). For each anchored assignment whose local clock is ahead
# of PM AND whose observation wouldn't change PM (local_newer_is_noop), adopt PM's clock ONLY (not
# PM's fields — for assignments WE are the authority) → LWW parity, churn stops. A genuine pending
# change (observation differs) is LEFT for the reconcile. App-role local write; read-only PM.
# The durable backstop is the apply_record local-newer no-op gate (deployed with the sidecar);
# this one-shot converges the EXISTING skew before the sidecar resumes. Idempotent (no-op at
# parity). Exit 0 clean / 2 auth / 3 empty-cohort abort.
python -m usa_wa_sync_powermap.heal_assignment_clocks --dry-run
python -m usa_wa_sync_powermap.heal_assignment_clocks

# Subscription prune (#73 Axis 1 step 6) — one-shot reclaim. build_reconciler narrowed the
# subscription set to the mirror set (jurisdiction lineage ∪ OUR anchored producer rows), but
# sync_subscriptions is additive, so the ~1,000 PM-only strangers the old whole-subtree walk
# registered stay subscribed-but-inert (feed delivers, reconciler fetch-then-skips them). This
# diffs PM's list_subscriptions against the freshly-discovered mirror set and unsubscribes the
# difference. Guarded: empty desired-set aborts (empty_desired), stale fraction over
# --max-prune-fraction aborts (prune_floor, default 0.9 — permissive since the first run removes
# ~half). Strangers have no local row (nothing evicted).
# Exit 0 clean / 2 auth / 3 aborted. RE-RUN TO CONVERGENCE: PM auto-subscribes the producer on
# observation write, so a concurrently-draining outbox regenerates a shrinking residual — the
# first pass over a busy system removes the bulk, then re-run until a --dry-run shows stale=0
# (best run when the outbox is quiescent). Observed 2026-07-07: 1226 → 303 → 31 → 0.
python -m usa_wa_sync_powermap.prune_subscriptions --dry-run
python -m usa_wa_sync_powermap.prune_subscriptions   # re-run until dry-run shows stale=0
```

## Provenance & integrity

```bash
# Provenance integrity sweep (#54/#55) — re-hashes stored RawPayload bodies against
# their FetchEvent.content_hash baseline; a divergence is corruption/tamper at rest.
# NULL baselines (pre-#54 legacy) are counted as "unbaselined", never a mismatch.
# Exit 0 clean / 1 mismatch (the non-zero the #49 OnFailure handler emails on).
# The default run is a ROLLING byte-slice (#55): it verifies --byte-budget (default
# 256 MiB) worth of payloads past a persisted ULID watermark and wraps at the archive
# tail, so per-run cost stays flat as the #39 docket volume grows (whole corpus
# re-verified every ceil(bytes/budget) runs). Its one write is the cursor upsert on
# clearinghouse_core.integrity_sweep_state (app-role DML; not the provenance tables).
# --full forces a whole-corpus pass ignoring the cursor (post-incident audit);
# --limit N is a row-capped partial (surfaced as limited). Prod runs this weekly
# (Sun 08:00 UTC) via usa-wa-integrity-sweep.timer.
python -m clearinghouse_core.integrity                # rolling slice (resumes + wraps)
python -m clearinghouse_core.integrity --full         # whole corpus, ignore cursor
python -m clearinghouse_core.integrity --limit 500    # row-capped partial

# One-off provenance repair (#64) — OWNER ROLE. The pre-#54 committees:2025-26 fetch
# events have NULL content_hash but DID archive their bodies, so backfill
# content_hash = sha256(RawPayload.body) — converting them to integrity-verified while
# keeping the fetch history + bytes (no deletion). Payload-less NULL-hash events are
# skipped+counted. Idempotent. Needs DATABASE_URL_OWNER (the app role is REVOKEd UPDATE
# on the ledger, #54). --dry-run previews.
python -m usa_wa_adapter_legislature.baseline_unbaselined_committees --dry-run
python -m usa_wa_adapter_legislature.baseline_unbaselined_committees
```

## Discovery probes (write-free)

Talk to WSL directly (NOT the runner) — no FetchEvent/RawPayload written. Answer
scoping questions ("how much history exists", "is the Id stable") before ingest.

```bash
# Committee historical extent probe (#64) — walks bienniums backward from current, tallying
# committee/meeting counts + meeting wire bytes, stopping after N consecutive empty bienniums.
python -m usa_wa_adapter_legislature.probe_committee_extent
python -m usa_wa_adapter_legislature.probe_committee_extent --start-biennium 2025-26 --max-empty 2

# Member Id-stability probe (P1b #27 step 0) — answers "is the WSL member Id a stable
# Person.source_id?" before member ingest: matches members BY NAME (not Id) across GetSponsors
# vs GetActiveCommitteeMembers (cross-endpoint) and GetSponsors(current) vs GetSponsors(prior)
# (cross-biennium), tallying Id agreement. Finding 2026-07-06: Id stable both axes → canonical
# source_id = GetSponsors.Id. --json for compact output.
python -m usa_wa_adapter_legislature.probe_member_identity
python -m usa_wa_adapter_legislature.probe_member_identity --biennium 2025-26 --json
# Deep-history sweep (#81): every consecutive biennium pair 1991-92→current, classifying
# same-name/different-Id divergences into re-keys (same District — forks one person) vs name
# collisions (different District — two people the Id separates). Finding 2026-07-08: Id STABLE
# across all 17 boundaries, 0 re-keys (one benign collision: two "Brian Sullivan"s, LD29/LD21).
python -m usa_wa_adapter_legislature.probe_member_identity --history
```

## Historical backfill (epic #76 / sub-project 3)

Sweep a source to its floor. Data-source-respecting: each closed biennium is
archived once (#54) and cache-hits on re-run; `--pause-seconds` drips against WSL
via the central rate limiter. `--dry-run` rolls back. Run-once (not timers).

```bash
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

# Historical member (sponsor) harvest — Phase A of the #76 backfill epic (#77). Sweep
# GetSponsors(biennium) from the 1991-92 floor to current through AdapterRunner(fill_only=True),
# archiving each sponsors:<biennium> wire (#54) and materializing PERSONS + wa_legislature_member_id
# identifiers ONLY (the sponsor normalize is persons-only, #78-2c — party/seat/committee tenure
# are merged spans built in Phase B #78, not per-biennium here). Persons dedup by stable Id (#81). Same
# op/resource key as the daily path. Pacing is central: --pause-seconds sets the WSL limiter.
# Closed biennia cache-hit on re-run; --dry-run rolls back; --force re-materializes.
python -m usa_wa_adapter_legislature.harvest_sponsors --dry-run   # 1991-92→current, roll back
python -m usa_wa_adapter_legislature.harvest_sponsors --from-biennium 1991-92 --pause-seconds 1

# Historical member SPANS — Phase B of the #76 backfill (#78). Archive-derived, no WSL pull:
# reads every archived sponsors:<biennium> offline (SponsorRosterCohortProvider re-parses via
# parse_sponsors), projects rows to party + Senate-seat tenure Observations
# (sponsor_observations), collapses contiguous biennia into merged valid_from..valid_to spans
# (tenure_spans.build_tenure_spans — a dormancy gap splits; the run reaching the current
# biennium stays open/is_active), and emits ONE Assignment per tenure keyed on the tenure
# start (sponsor_span_emit) with a Citation per biennium in range (cite-every-biennium, #78).
# Idempotent re-assert. Depends on the #77 harvest archiving the rosters first. --dry-run rolls
# back. The daily refresh also re-drives this builder for the current biennium (#78-2c).
# Ends with the #83 stale-span sweep (party + chamber-senate): open spans the rebuild no longer
# asserts are closed (departed members) — closed_stale in the completion log; closed_stale > 0 on
# an unrestricted run = previously-stranded rows repaired. Guarded against empty/mass closes
# (sweep_aborted=true in the completion log); --max-close-fraction 1.0 (validated to (0, 1])
# permits a deliberate one. STALE-ROW EXCLUSION (#105 (b)): each biennium's named rows are
# screened against that biennium's committee-roster archive (roster_hygiene) — a departed-but-
# still-named ghost (Kilduff/Senn/Nguyen) emits no party/Senate observations for its stale
# bienniums, so the merged span ends at the real departure boundary (sponsor_stale_row_excluded
# per exclusion; --stale-min-coverage floor, default 0.9, skips a thin committee cohort —
# stale_exclusion_skipped_low_coverage; >1 disables).
python -m usa_wa_adapter_legislature.harvest_sponsor_spans --dry-run
python -m usa_wa_adapter_legislature.harvest_sponsor_spans

# Span MIGRATION — #78-3 + #97, OWNER ROLE (deletes citations, #54). Collapse STRANDED
# party/chamber-senate Assignments (each carrying a pm_assignment_id) onto the merged span that
# shares their (person_id, role_id) — PM's own structural assignment key. Transfers the PM anchor
# to the span + hard-deletes the stranded row + its citations, so the local cache holds ONE row per
# PM assignment (else the assignment descriptor's local_match scalar_one_or_none / the #86 unique
# index breaks). Builds the spans first (idempotent), then collapses. Two stranded shapes:
#   (1) pre-#78 per-biennium 3-part rows ({member}:{dim}:{YYYY-YY}), #78-3; and
#   (2) superseded 4-part shallow spans (#97) — the 2c daily path keys a span on the CURRENT
#       biennium start; when the full-natural-depth backfill (harvest_sponsor_spans, no restrict)
#       merges the same tenure into an EARLIER-start span, the current-start row is stranded (the
#       same _superseded_pairs case #91 fixed for PDC House / #95 for committees). The #78-3 pass
#       only handled shape 1, so on the 2c deploy the 202 4-part current rows were left uncollapsed
#       as orphans_no_span — #97 closes that. Anchor transfer is index-safe (delete+flush before
#       assign → runs under the live uq_assignments_pm_assignment_id #86 index).
# Leaves chamber-house (PDC/#79) + committee (#82) rows untouched; a stranded row with no covering
# span is left + counted (orphans_no_span); a keeper already carrying a different anchor drops the
# stranded one (anchors_dropped + warned, the #80 orphaned-upstream case). Idempotent; --dry-run
# rolls back. #97 run (full-depth Senate/party backfill): spans_built=920 superseded_retired=164
# anchors_transferred=164 orphans=0 → Senate 241 spans (1991->2025) + party 679, all produced.
# DEPLOY SEQUENCING: run in the SAME window as the backfill, sidecar PAUSED
# (sudo systemctl stop usa-wa-sync-powermap). PM keys assignments on (person, role, start_date), so
# a deepened span the sidecar anchors BEFORE this runs gets its own PM assignment, after which the
# stranded anchor can only be dropped (anchors_dropped). Restart after:
# sudo systemctl start usa-wa-sync-powermap.
python -m usa_wa_adapter_legislature.migrate_sponsor_spans --dry-run
python -m usa_wa_adapter_legislature.migrate_sponsor_spans

# Committee MEMBERSHIP harvest — Phase A (#82). Enumerate each biennium's House/Senate standing
# committees from the local committees-roster archive (no extra GetCommittees call; an un-archived
# biennium falls back to a live, UNARCHIVED GetCommittees pull — run harvest_committees first if
# you want the enumeration itself provenanced) and fan
# GetCommitteeMembers(biennium, agency, Name) over them, archiving each wire (#54). Persons only
# (fill_only) — membership is a Phase B span. Joint/Other skipped (no membership op, #39). Floor
# 1999-00 (below it WSL's truncated old names fault → swallowed to an empty roster). ~40
# committees x ~14 biennia; --pause-seconds sets the central WSL limiter. Closed rosters cache-hit.
python -m usa_wa_adapter_legislature.harvest_committee_members --dry-run
python -m usa_wa_adapter_legislature.harvest_committee_members --from-biennium 1999-00 --pause-seconds 1

# Committee membership SPANS — Phase B (#82). Archive-derived, no WSL pull: re-parses each
# archived committee-members-hist roster offline, projects (member, committee, biennium)
# observations, merges contiguous biennia into one membership span bound to the committee's
# shared `member` Role, citing each (biennium, committee) roster. A dormancy gap opens a second
# span. Idempotent. The daily refresh re-drives this for the current cohort.
# Ends with the #83 stale-span sweep (committee): open memberships the rebuild no longer asserts
# are closed — a member who left the committee OR the legislature, and superseded-wire orphans.
# closed_stale in the completion log; guarded against empty/mass closes (sweep_aborted=true when
# tripped). A wholesale WSL committee-Id re-key makes EVERY old-Id span stale at once — that
# legitimate mass close is the --max-close-fraction 1.0 case (flag validated to (0, 1]).
python -m usa_wa_adapter_legislature.harvest_committee_member_spans --dry-run
python -m usa_wa_adapter_legislature.harvest_committee_member_spans

# Committee span MIGRATION — #82, OWNER ROLE, run AFTER the Phase A harvest deepens spans.
# A span starting at a legacy row's biennium upserts it in place (same 4-part key), so a shallow
# archive needs no migration. Once the harvest pushes a span's start earlier, the shipped
# per-biennium row is stranded: legacy = a committee Assignment the emitted span-key set doesn't
# claim. Each is mapped to the covering span by (person_id, role_id) + validity window, its
# pm_assignment_id transferred, then hard-deleted with its citations (owner-only under #54).
#
# SEQUENCING: run this in the SAME maintenance window as the Phase A harvest, with the sidecar
# paused (sudo systemctl stop usa-wa-sync-powermap). PM keys assignments on
# (person, role, start_date), so a deepened span the sidecar drains first is minted as its OWN PM
# assignment — after which the legacy row's anchor can only be dropped, orphaning that PM row
# (a live PM assignment with the wrong start_date and no local mirror). Those are counted
# `anchors_dropped` and warned per row; expect 0. Restart the sidecar after.
# Idempotent; --dry-run rolls back.
python -m usa_wa_adapter_legislature.migrate_committee_spans --dry-run
python -m usa_wa_adapter_legislature.migrate_committee_spans

# Reclassify generic `member` Roles → PM catalog slugs (#110). Two emitters historically
# stamped every membership Role with role_type='member', but PM's role_types catalog refines
# that into `committee_member` / `party_member` (power-map#268). The classifier sat permanently
# diverged from PM's role_type_slug, so the #109 no-op gate read a genuine diff and re-enqueued
# ~305 roles every reconcile forever. The emitters now stamp the catalog slug on NEW rows; this
# reclassifies EXISTING ones by source_id prefix (committee-member-role: → committee_member,
# party-role: → party_member) — get_or_create_role never rewrites an existing classifier and
# the daily refresh only re-drives the current cohort, so historical rows need this one-shot.
# Reclassifying makes to_observation send the matching slug → the next reconcile's gate reads a
# true no-op and adopts PM's clock, stopping the churn. APP role (role_type is a plain canonical
# column). Idempotent; --dry-run rolls back. No sidecar pause needed (local reclassify only).
python -m usa_wa_adapter_legislature.migrate_member_role_types --dry-run
python -m usa_wa_adapter_legislature.migrate_member_role_types

# Committee historical backfill (sub-project 3, Phase A) — sweep GetCommittees(biennium)
# over a range through AdapterRunner(fill_only=True): archive the full-roster wire under
# committees-roster:<biennium> + materialize standing committees by stable Id WITHOUT
# clobbering PM-curated rows (#65). Hits live WSL (one POST/biennium, --pause-seconds
# between); auto-probes the floor if --from-biennium omitted; closed rosters cache-hit on
# re-run. --dry-run rolls back. Distinct from the daily GetActiveCommittees archive.
# --force re-fetches + re-normalizes past the freshness cache (a plain re-run inside the
# 1-day TTL is a cache hit that upserts NOTHING) — the post-incident re-materialization of
# rolled-back rows, and the retrospective-change revalidation of closed rosters; byte-identical
# wire dedups to the existing RawPayload, fill-only leaves unaffected committees untouched.
# FOLLOW-UP after a --force run that CREATES committees: the freshly-created rows are
# LWW-locked (local updated_at ≥ PM's org clock), so the sidecar mirror won't adopt their
# PM name/acronym windows until PM's clock advances — run `heal_committee_curation` to
# force-adopt them (else validate_committees shows them divergent with empty child tables).
python -m usa_wa_adapter_legislature.harvest_committees --from-biennium 2011-12 --pause-seconds 2
python -m usa_wa_adapter_legislature.harvest_committees --dry-run   # auto-probe floor, roll back
python -m usa_wa_adapter_legislature.harvest_committees --from-biennium 1991-92 --force  # re-materialize
# then: python -m usa_wa_sync_powermap.heal_committee_curation   # mirror the created cohort's windows

# Full committee rename-chain emission (sub-project 3, Phase B) — the deep-history sibling
# of #46. Reads every archived committees-roster:<biennium> offline (archive-first, no WSL
# re-pull), builds each stable Id's full normalize_name(LongName) timeline, and emits every
# former->legal transition to PM (windowed dated-name evidence). Dormancy-aware + per-boundary
# storm floor. Emit-only; PM curates is_canonical, the #45 mirror brings windows back (now
# sticking via #65). Backfill-once (not a timer). --dry-run previews; exit 0/1/2/3.
python -m usa_wa_sync_powermap.reconcile_committee_name_chain --dry-run
python -m usa_wa_sync_powermap.reconcile_committee_name_chain
```

## Submodules

The `skills-vendor/` directory holds upstream skill repos as submodules. They are updated automatically by the `UserPromptSubmit` hook in [`.claude/settings.json`](../.claude/settings.json), but the manual commands are:

```bash
# Initialize submodules on a fresh clone
git submodule update --init --recursive

# Update vendored skills to the latest upstream main
git submodule update --remote --merge skills-vendor/gregoryfoster-skills skills-vendor/obra-superpowers
```
