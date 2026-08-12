# Commands — seat-fact backfills

The Layer-3b `usa-wa-facts-seats` historical builders: PDC winner-cohort identifier
links and the WSL+SOS House Position seat. Split out of [COMMANDS.md](COMMANDS.md),
which is where the index lives; the Layer-3 adapter sweeps they depend on are in
[COMMANDS-BACKFILL.md](COMMANDS-BACKFILL.md).

## PDC historical backfill (#79)

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
python -m usa_wa_adapter_pdc.harvest --dry-run
python -m usa_wa_adapter_pdc.harvest --from-year 2008 --pause-seconds 0.5

# Phase B — era-matched IDENTIFIER build (archive-first, no live PDC pull; identifier-only since
# #101): each cohort pairs with its seating biennium's sponsor roster — an even year seats the
# NEXT biennium (2012 → 2013-14), an odd special seats the biennium STARTING that year (2025 →
# 2025-26, #121) — matches each winner to a WSL Person, emits person_wa_pdc links. A cohort
# seating a FUTURE biennium (the just-run November even general, archived Nov-Dec) is skipped +
# logged (pdc_cohort_future_biennium_skipped) until its roster exists — the next cycle links it
# (#121 CR; the rollover-readiness audit is #135). The House Position SEAT is no longer built
# here (that is usa_wa_facts_seats.house.build, below). Idempotent.
python -m usa_wa_facts_seats.pdc.build_pdc_spans --dry-run
python -m usa_wa_facts_seats.pdc.build_pdc_spans

# Migration — OWNER ROLE, run AFTER build_pdc_spans, sidecar paused. Retires the pre-#79
# per-biennium usa_wa_pdc House rows ({member}:chamber-house:{biennium}, 3-part) stranded by the
# 4-part span key: maps each to the covering span by (person, role) + window, transfers the PM
# anchor, hard-deletes the row + its citations (owner-only under #54). A row with no covering span
# yet is left as orphans_no_span (re-run after the build). anchors_dropped (>0) = the sidecar
# anchored the span first, orphaning the legacy PM assignment (the #80 start-date gap).
python -m usa_wa_facts_seats.pdc.migrate_pdc_spans --dry-run
python -m usa_wa_facts_seats.pdc.migrate_pdc_spans
```

## WSL+SOS House Position backfill (#101)

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
# `cohorts_skipped` (only this raises the whole-source outage warning), a no-legislative-race year
# with no CSV (2021/2023) is the expected `cohorts_absent`, rolled back per-SAVEPOINT while the
# reached years commit. EXIT 0/1/2, plus 4 = every year skipped — NEW at #179b; see MODULES-SOS.md.
python -m usa_wa_adapter_sos.results.harvest --dry-run
python -m usa_wa_adapter_sos.results.harvest --from-year 2008 --pause-seconds 1.0

# Phase B — WSL+SOS House Position span build (archive-first, no live pull): the sitting House
# roster (WSL sponsor archive) x the SOS results archive (the Position) -> merged usa_wa_legislature
# state_representative Position seat spans, cite-every-biennium onto sos-legresults:<Y>. A sitting
# member with no resolvable SOS position gets no seat (OQ1: emit nothing, counted missing_position)
# — UNLESS within-LD elimination (#103) resolves it: an LD with exactly 2 sitting members, 1
# ballot-claimed seat and 1 unmatched member gives that member the remaining position (a
# mid-biennium appointee, or a ballot<->roster name change). Inferred (member, biennium) pairs cite
# the sponsor roster, log house_seat_inferred, and surface as coverage["inferred"].
# DEPENDS ON Phase A + the WSL sponsor archive/Persons (#77).
# Ends with the #83 stale-span sweep (usa_wa_legislature, chamber-house); same mass-close guard
# (--max-close-fraction, (0,1], 1.0 disables). --biennium scopes to a biennium's current members
# (each keeps full history). ROSTER HYGIENE (#105): each biennium's roster sheds (a) mover rows
# — a House row whose Id also appears in a named Senate row of the same wire (Alvarado/Hunt;
# house_roster_mover_excluded) — and (b) committee-corroborated stale rows — a named member
# absent from that biennium's committee-roster archive (Senn/Kilduff ghosts;
# sponsor_stale_row_excluded), guarded by --stale-min-coverage (default 0.9: a biennium whose
# committee cohort names <90% of the wire's named members skips the exclusion —
# stale_exclusion_skipped_low_coverage — so a thin archive never reads as mass departure; >1
# disables entirely) AND by the tail rule (excluded only when committee-absent in that biennium and
# every later one — later presence = an archive gap: stale_exclusion_rescued_by_later_presence).
# Both un-block the #103 elimination and drop the ghost's seat assertion so the sweep closes it.
# PRE-2009 BACK-CHAIN (#118 Phase 1): the SOS ballot floors at the 2008 general, so a pre-2009 House
# member has no ballot to position. A WA rep holds a Position continuously, so this walks the
# archived biennia newest->oldest and carries each ballot-anchored Position back through
# uninterrupted same-LD tenure, letting the #103 elimination resolve the mate each biennium.
# Reaches 2003-04..2007-08; the 1991-2001 era has no reachable anchor (#140). Back-chained seats
# cite the sponsor roster, log house_seat_backchained, surface as coverage["seeded"]. Guardrails: a
# redistricting era break (1993-94/2003-04/2013-14/2023-24 — WA keeps LD numbers, so the break is
# explicit) and an LD move / tenure gap both stop the chain; --max-backchain-hops caps the depth
# (default 4; 0 disables). Runs in BOTH the daily re-drive and this backfill (idempotent). Only
# ballot-class positions carry back — an elimination-only mate does not seed its own earlier
# tenure (that recursive cascade is Phase 2, deferred).
python -m usa_wa_facts_seats.house.build --dry-run
python -m usa_wa_facts_seats.house.build

# Migration — OWNER ROLE, one-shot, run AFTER usa_wa_facts_seats.house.build. TWO passes:
# (1) #103 within-source superseded collapse FIRST — elimination deepens some tenures, so an
# existing anchored usa_wa_legislature row can be superseded by a new deeper-start row of the same
# seat (the #97 sponsor pattern); each collapses onto its earlier-start covering keeper
# (superseded_retired), transferring the anchor — a keeper that merged in place already carries its
# own anchor, so the superseded one is dropped + warned (one PM assignment orphaned upstream, #80).
# (2) The #101 PDC re-source collapse: retires existing usa_wa_pdc 4-part chamber-house rows onto
# the SURVIVING usa_wa_legislature span that COVERS them (mapped by (person, role) + validity
# window — NOT exact source_id: PDC omits the pre-2018 position, so a cross-2018 incumbent's
# existing PDC span is shallow …:2019-20 while the SOS builder emits a deeper …:2017-18). Transfers
# the PM anchor, deletes the retired row + its citations (owner-only #54). A PDC row with no
# covering keeper is orphans_no_keeper; 3-part legacy rows are migrate_pdc_spans's job
# (skipped_legacy). Idempotent; --dry-run.
python -m usa_wa_facts_seats.house.migrate --dry-run
python -m usa_wa_facts_seats.house.migrate

# DEPLOY SEQUENCING (the whole historical backfill — and any build that changes span depth, e.g.
# enabling #103 elimination), SIDECAR PAUSED throughout, before the next 06:45 SOS timer fire.
# Build BEFORE migrate, so the deep usa_wa_legislature keeper spans exist for the migration to
# collapse the stranded PDC + superseded rows onto (transferring their anchors) before anything
# drains to PM. Draining first lets PM dedup-match a new span onto a still-anchored old row's
# assignment ((person, role, start_date)) and park the entry UNAVAILABLE (#86 + operator alert).
#   sudo systemctl stop usa-wa-sync-powermap
#   python -m usa_wa_adapter_sos.results.harvest --from-year 2008        # Phase A (SOS results archive)
#   python -m usa_wa_facts_seats.house.build                   # Phase B: full-depth rebuild
#   python -m usa_wa_facts_seats.house.migrate                # OWNER role: superseded + PDC->WSL
#   sudo systemctl start usa-wa-sync-powermap                  # let the sidecar drain to PM
# If the 06:45 timer beats this window: the daily build emits the new spans first and the sidecar
# parks the colliding entries UNAVAILABLE (#86, operator alert) — recoverable: run the migrate,
# then redrive (python -m usa_wa_api.cli.redrive).
```
