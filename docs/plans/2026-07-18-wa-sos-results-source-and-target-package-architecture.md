---
title: WA SOS results source + multi-source target-package architecture
date: 2026-07-18
status: draft
---

# WA SOS results source + multi-source target-package architecture

Unblocks usa-wa#101 (the merged House re-partition depends on a working SOS position source).
Related: usa-wa#100 (votewa filings adapter), usa-wa#75 (Senate seat precedent), usa-wa#99
(filings candidacy metadata). Not a supersession of the #101 plan — the #101 *application*
(WSL+SOS House Position seat, `usa_wa_legislature`-sourced) stands; only the SOS *source* under
it changes, and the package gains a documented multi-source shape.

## Problem

#101 shipped the House Position seat sourcing its ballot Position 1/2 from the votewa
`ExportToExcel` **filings** export. Live audit (2026-07-18) found WA SOS retired that export for
elections **2020+** (migrated to Power BI) — it returns HTTP 500, so the daily SOS driver (which
needs the current biennium's 2024 cohort) and **all current-member House positioning are broken**.
The prod migration was correctly held (dry-run preview caught it; nothing was archived).

An audit of an alternate source — `results.vote.wa.gov` legislative **results** — shows it covers
**2008–2024 including the current cycle**, with unique value (ballot Position *and* vote counts).
But it is a genuinely *different* source with its own contract and quirks, surfaced in advance:
per-year filenames discoverable only via each election's `export.html` (2012 carries a
certification timestamp), **three** race-label variants (`State Representative Pos. N` ~99%, plus
`Representative, Position N` at 2020 LD15 and a bare `State Representative 2` at 2014 LD30 — real
seated members that an exact-match parser would silently drop), a write-in era-shift (0 rows
pre-2020, ~123 after), and party-label variants (`GOP`, `States No Party Preference`, …).

Two needs, then: (1) add this results source **as its own self-contained archive** (not a
replacement — filings retain unique candidacy metadata for 2008–2018, #99), and (2) establish and
**document** the general pattern for bundling multiple sources from one *target* under one package
with a clean sourcing/application separation, so future SOS feeds (voter pamphlets, precinct data)
and other jurisdictions follow it.

## Approach

One adapter package per **jurisdiction+target** (`usa-wa-adapter-sos` = "everything WA Secretary of
State"). Inside it, each **source** is a self-contained archive — its own `Source`/`source_slug`,
archive key, transport, adapter, normalize, cohort provider, and harvest — and the **application**
layer (House Position seat: projector, span builder, emitter, migration, daily refresh) is
source-agnostic, consuming a *cohort interface* rather than a specific source. Reorganize the
existing flat modules into `filings/` (the votewa source, unchanged behavior) + `house/` (the
application), add a new `results/` source for `results.vote.wa.gov`, repoint the House builder from
the filings cohort to the results cohort, harden the harvest against per-year gaps, and document
the pattern in `docs/ARCHITECTURE.md` (referenced from AGENTS.md). TDD throughout; the eventual
sidecar-paused prod migration is unchanged, now merely unblocked by a source that serves 2020+.

## Tradeoffs / alternatives

- **New package `usa-wa-adapter-sos-results`** — rejected (your call, and correct): a package is
  the jurisdiction×target unit; a per-feed package fragments one target's archives and its
  provenance/deploy story.
- **Extend the filings adapter to also fetch results (one adapter, two endpoints)** — rejected:
  conflates two sources' provenance, retention, cadence, and failure modes; violates
  "self-contained archive per source" and muddies which wire a `RawPayload` is.
- **Replace filings with results (drop filings)** — rejected (yes-and): filings carry unique
  candidacy metadata (email/address/filing-date/withdrawal, #99) for 2008–2018; both have value.
- **Keep the flat module layout, add `results_*.py` alongside** — rejected as the end state: flat
  names (`transport.py`, `results_transport.py`) stop signalling which source they serve; the
  reorg cost is low because the House builder is being repointed anyway.
- **Guess the results CSV URL per year instead of traversing `export.html`** — rejected: the 2012
  timestamped filename proves the URL isn't derivable; traversal is the only robust discovery.

## Steps

1. **`docs/ARCHITECTURE.md` + AGENTS.md reference.** Document the layered pattern as a reusable
   convention: package = jurisdiction+target; **source** = self-contained archive (own
   `Source`/`source_slug`/archive-key/transport/adapter/normalize/cohort/harvest, its own
   provenance); **application** = source-agnostic spans/seats consuming a cohort interface; the
   four-layer clearinghouse split it lives within. Reference it from AGENTS.md § Project Layout.
   *Verifiable:* file exists, AGENTS links it, describes source-vs-application separation with the
   SOS filings/results example.
2. **Reorganize the existing package into `filings/` + `house/` (pure move, no behavior change).**
   `transport/adapter/normalize.filings/sos_cohort/harvest_sos` → `filings/`;
   `build_house_spans/house_span_emit/migrate_house_source/refresh/normalize.house_seats` →
   `house/`. Rename `SOSClient` → `SOSFilingsClient`. Update every import, the test tree mirror,
   AGENTS/COMMANDS, and the `usa-wa-sos-refresh.service` ExecStart module path.
   *Verifiable:* full suite green with only import/path deltas; ruff clean; no logic diff.
3. **`results/` source — transport (TDD).** `SOSResultsClient`: `general_election_date(year)`
   (first Tue after first Mon of November → YYYYMMDD), `export.html` traversal to discover the
   `export/<date>_Legislative[_<ts>].csv` href (handles the timestamp variant), fetch with
   redirect-follow, `WireFetch` (archived bytes #54 + decoded rows) + offline re-parser; central
   courtesy min-interval gate (the #77 pattern, `results.vote.wa.gov`). *Verifiable:* transport
   tests against the saved 2024/2022/2012 fixtures + the export.html traversal + the redirect.
4. **`results/` source — adapter + provisioning + normalize (TDD).** `ResultsAdapter`
   (source_slug `usa_wa_sos_results`, archive-only, key `sos-legresults:<YYYYMMDD>`); provision the
   second `Source`; **robust race-label parser** — for a `Race` containing "Representative" (not
   "Senator"): `LD` = digits after "DISTRICT", `position` = the trailing `1|2`, case-insensitive —
   covering all three audited variants; filter `WRITE-IN`; extend party canonicalization
   (`GOP`→republican, `States No Party Preference`, …). *Verifiable:* unit tests assert LD15-2020
   (`Representative, Position 1/2` → Chandler/Dufault) and LD30-2014 (bare `State Representative 2`
   → Freeman/Dovey) parse correctly, and Senate rows are ignored.
5. **`results/` cohort provider + resilient harvest (TDD).** `SosResultsCohortProvider`
   (archive-first `{election_year: {LD: [HousePosition]}}` + per-year citation events, joins
   `RawPayload` — the #82 lesson); `harvest_results` sweeps even years 2008→current, **per-year
   resilient**: a 404/500/absent year is skipped-and-logged and the *reached* years commit (fixes
   the current all-or-nothing sweep that rolls back on one bad year). *Verifiable:* a harvest test
   with a mid-sweep failing year commits the rest and logs the skip; the cohort re-parses offline.
6. **Repoint the `house/` builder to the results cohort.** `build_house_position_spans` + `refresh`
   consume `SosResultsCohortProvider`; the projector's `{LD:[position]}` lookup is unchanged (same
   shape from either source). *Verifiable:* an end-to-end test builds a `usa_wa_legislature` House
   span from a results archive; the #100 finding-1 regression test still holds; a current-biennium
   (2024) member is positioned.
7. **Deploy wiring + docs sweep.** `usa-wa-sos-refresh.service` runs the results harvest + house
   rebuild; COMMANDS.md/AGENTS.md/README updated for the new source, its harvest CLI, and the
   `sos-legresults:` archive; the #101 migration runbook points at the results archive.
   *Verifiable:* `test_unit_ordering` + `systemd-analyze verify` pass; docs match the module tree.

The prod realization (harvest results → build house spans → `migrate_house_source` → install the
SOS timer → resume sidecar, owner-role + sidecar-paused) is unchanged and remains a deliberate
operational window **after** this plan lands and re-audits clean.

## Open questions / risks

- **Reorg churn on just-merged #101 code** — mitigated: step 2 is a pure move validated by the
  green suite, and the House builder is repointed regardless (filings can't serve 2020+). Doing it
  now (vs. later) avoids a second churn.
- **`usa_wa_sos_results` source_slug / `sos-legresults:` key** — proposed names; confirm before
  step 4 (a slug is hard to change post-archive, though prod has 0 SOS rows today, so free now).
- **Scope of the results archive** — it captures whatever the Legislative CSV holds (Senate rows
  included, available to #75 later); this plan's *application* is House Position only. Odd-year
  generals / other offices are out of scope.
- **Future-year fetch (2026 not yet held)** — the daily driver targets the current biennium's
  seating election (2024, present); a fetch of an unheld year 404s → step 5 resilience absorbs it.
- **Party-label completeness** — sitting members are ~all clean D/R (party is a match tiebreaker),
  so minor-party canonicalization is best-effort, not coverage-critical.
- **Migration unchanged** — still owner-role + sidecar-paused; this plan only swaps the source the
  keeper spans are built from.
