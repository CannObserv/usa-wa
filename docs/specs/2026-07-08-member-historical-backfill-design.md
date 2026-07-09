# Historical Member (Sponsor) Backfill — WSL + PDC, Merged-Span Tenure

**Date:** 2026-07-08
**Status:** Design (epic [#76](https://github.com/CannObserv/usa-wa/issues/76); sub-issues #77–#82)
**Scope:** Backfill the full historical WA legislator record both sources carry —
members, party, chamber seats, committee membership — as local canonical
Person/Role/Assignment data with merged-span tenure, and produce it to Power Map.

## Context

Today the member cluster is ingested **current-biennium only**: `refresh.py` pulls
`GetSponsors(current)` and materializes Persons + party + Senate seats for whoever
is seated now; the PDC refresh (#69/#75) enriches the current cohort. So any
legislator who left before the current biennium has **no local row** and nothing on
PM from us — surfaced in the #75 discussion (Karen Keiser, a 2022 Senate winner who
retired end of 2024, has no `Person` and an untouched PM entry).

The intent now is to backfill **everything the sources carry**, to each source's
own floor.

### What the sources carry (probed 2026-07-08)

| Source | Op | Reach | Key | Yields |
|--------|----|-------|-----|--------|
| WSL | `GetSponsors(biennium)` | **1991-92 → current** (~18 biennia; 1989-90 faults) | stable member `Id` | Person, `wa_legislature_member_id`, party, Senate seat |
| WSL | `GetCommitteeMembers(biennium, agency, Name)` | **~1999-00 → current** | committee short `Name` (LongName faults) | committee **membership** |
| PDC | Campaign Finance Summary `3h9x-7bvm` | **2009 → 2025** (even-year cohorts from 2010) | PDC `person_id` | House **Position** seat, `person_wa_pdc` |

WSL member `Id` is stable across biennia (Id 7 appears in both 1991-92 and 1993-94);
this is the load-bearing dedup premise, validated at depth in #81 before minting.

### Decisions (locked with the epic)

- **Depth: each source to its own floor** — members/party/Senate-seat to 1991-92;
  committee membership to ~1999-00; PDC enrichment wherever it exists (~2010+).
- **Tenure: merged contiguous spans** — one Assignment per contiguous
  same-seat/same-party/same-committee tenure (`valid_from..valid_to`), not one per
  biennium. A 12-year senator is one span, not six.
- **PM production: in scope** — the historical cohort is produced to PM this effort,
  with batching/backpressure.
- **Committee membership: in scope** — `GetCommitteeMembers` is the historical
  sibling of `GetActiveCommitteeMembers`.

## Goals

- Archive the pristine wire for every historical `sponsors:<biennium>`, PDC
  `{house,senate}-winners:<year>`, and committee-member window (FetchEvent +
  RawPayload + `content_hash`, #54), once per window.
- Materialize every historical **Person** keyed by stable WSL `Id` (fill-only).
- Express every tenure (party / chamber seat / committee membership) as a
  **merged span** with correct `valid_from..valid_to` and `is_active`.
- Produce the whole cohort to PM.

## Non-goals

- **Joint/`Other` committee membership** — no `CommitteeService` membership op;
  those orgs are meeting-derived (#39) and have no roster source.
- **Pre-~1999 committee membership** — `GetCommitteeMembers` faults on the truncated
  old committee `Name`s; roster/party/seat still reach 1991-92.
- **Bills/sponsorships** — #28 (P1c).
- **Periodic re-validation of closed biennia** — the committee sub-project-4 analog.
- **House seats without a PDC Position** — the seat model enforces `qualifier` for
  `state_representative`, so a historical House member with no PDC winner row gets
  Person + party (+ committee membership) but no House seat. Coverage-limited.

## Architecture

Phase A/B decoupling exactly like the committee backfill: **Phase A** is
source-facing / archival / idempotent; **Phase B** derives entirely from the local
archive and can re-run/re-emit without touching the source.

### Phase A — Harvest + materialize Persons (fill-only) — #77

`python -m usa_wa_adapter_legislature.harvest_sponsors`

- Sweep `GetSponsors(biennium)` from the **1991-92 floor** to current through
  `AdapterRunner(fill_only=True)`. Each `sponsors:<biennium>` window archives its
  wire once (dedup-bounded) and materializes **Persons + `wa_legislature_member_id`
  identifiers only** — deduped by stable `Id`, so a member seen across biennia is one
  `Person`.
- **No assignments in Phase A.** Party/seat/committee tenure is a merged span, built
  in Phase B from the archive. (This is the divergence from the current inline
  emission — see the span engine.)
- Same op/resource key as the daily path (`sponsors:<biennium>`); historical biennia
  are just older resource ids — no new provenance key (unlike committees'
  `committees-roster`).
- `--from`/`--to`/`--pause-seconds`/`--dry-run`. ~18 sequential POSTs, dripped.
  Closed biennia cache-hit on re-run; fill-only never clobbers PM curation.

### Phase B — Merged-span tenure engine — #78

The heart of the "merged spans" decision, and the assignment analog of the committee
rename-chain builder: a **pure function** over the archived roster timeline.

**Input.** `{biennium: [member rows]}` re-parsed offline from the archived
`sponsors:<biennium>` wire (via `parse_sponsors`; archive-first, no re-pull), plus
the PDC House-seat observations (#79) and committee memberships (#82).

**Span key.** A tenure is identified by `(member_id, kind, discriminator)`:

| kind | discriminator | source |
|------|---------------|--------|
| `party` | party slug | GetSponsors |
| `chamber-senate` | LD | GetSponsors |
| `chamber-house` | LD + Position | PDC (#79) |
| `committee` | committee `Id` | GetCommitteeMembers (#82) |

**Span construction.** For each key, order its appearances by biennium and collapse
**consecutive** biennia into one span:

- `valid_from` = first biennium's Jan 1 (odd start year).
- `valid_to` = last contiguous biennium's Dec 31 (even end year) —
  **unless** the span includes the **current** biennium, in which case
  `valid_to = NULL` and `is_active = True` (the open end).
- `is_active = (span includes the current biennium)`.

**Deterministic `source_id`** = `{member_id}:{kind}:{discriminator}:{start_biennium}`.
Keying on the tenure *start* makes re-runs idempotent: an extending span keeps its
id (upsert updates `valid_to`/`is_active`); a dormancy gap opens a new-start span.

**Semantics that fall out of "consecutive":**

- **Dormancy breaks a span.** A member out for a biennium then back = two spans.
  Note the deliberate contrast with committees, where *absence ≠ retirement* (no
  `archived_at`): there we model entity *existence*; here we model a tenure *fact*
  ("served this biennium"), so an archive gap genuinely ends the tenure.
- **Chamber moves = separate spans.** `chamber-house` and `chamber-senate` are
  distinct keys, so a House→Senate mover naturally gets two spans that both touch the
  move biennium (GetSponsors returns two rows for a mover — the existing #74 shape).
- **Party / committee changes = new span** (changed discriminator → new key).

**Subsuming the current path.** Today `normalize_sponsors` emits per-biennium
open-ended assignments inline (`build_assignment` hardcodes `is_active=True`,
`valid_to=None`). Under spans that inline emission is removed; instead the daily
refresh, after archiving `sponsors:<current>`, **re-drives the span builder for the
members it observed** — the current biennium is just a span's open end. `build_assignment`
gains real `valid_to` + `is_active`. On a fresh deploy (archive holds only recent
biennia) spans are as deep as the archive and deepen after the harvest — correct.

**Migration — IMPLEMENTED (`migrate_sponsor_spans`, #78-3).** Span `source_id`s differ
from today's per-biennium `{id}:{dim}:{biennium}` keys, so the span row is a *new*
`Assignment` (new `id`), disjoint from the legacy rows. The migration does **not** reuse
the legacy `id`; instead it keys off PM's own identity. **The successor is matched on
`(person_id, role_id)`** — PM identifies an assignment structurally by `(person, role)`
(the descriptor's observation carries no source_id), and a span shares the legacy rows'
person + role, so PM already folds them onto one assignment. The migration mirrors that:
for each legacy party/`chamber-senate` row it finds the span with the same
`(person_id, role_id)`, **transfers the legacy `pm_assignment_id` anchor onto the span**
(so the span, not the deleted legacy row, is the single local representative of that PM
assignment — the descriptor's `local_match` `scalar_one_or_none` would otherwise break on
two rows sharing one anchor), then **hard-deletes the legacy row + its citations** (the
span carries its own cite-every-biennium set). Because PM matches structurally, the span's
first observation updates the *same* PM assignment — no duplicate is created. Scope is
`party` + `chamber-senate` only (the two dims the builder supersedes); `chamber-house`
(PDC/#69) and `committee` (#82) rows are left untouched, and a legacy row with no successor
span is left in place and counted (`orphans_no_span`), never orphaned. Idempotent
(a second pass finds no legacy rows). Run-once on the 2c deploy — prod carried 202 legacy
rows (151 party + 51 Senate), all `2025-26`, all PM-linked.

**Multi-tenure producer→PM (power-map#289, resolved).** The CR-6 evaluation filed power-map#289
worried that PM would fold two spans sharing `(person, role)` onto one assignment. Reviewing PM
confirmed the opposite: PM's match key is **`(person, role, start_date)` with NULLS NOT DISTINCT**
and has been since power-map#177 — distinct dated `start_date`s **coexist** as separate rows, so
our dated spans (each sends its `valid_from` as `start_date`) land as distinct tenures with no
collapse. #289 additionally shipped the id-addressed `NULL → dated` promotion path (date an
already-pushed undated tenure in place, rejecting a differing existing date). The migration's
covering-window match + legacy retirement stays correct *locally* — on the shallow prod archive a
span's `valid_from` equals its legacy row's `start_date`, so both resolve to the same PM
assignment and the migration collapses the double-anchor.

**Residual — start-date correction as the archive deepens (defer to #80).** One gap remains for
the *deferred* #77 production run: a merged span's `valid_from` moves **earlier** as the harvest
archives older biennia (a 2025-start span becomes a 2013-start span). Since the producer does
match-or-create by `start_date`, the deeper span mints a **new** PM assignment and orphans the
shallow one PM already holds — and #289's backfill only fills `NULL → dated`, not
`dated → earlier-dated` (a `start_date_conflict`). So deepening a span's start is a genuine
*correction*, not a fill; handling it (re-key/retire the orphan, or a PM start-correction path)
belongs to #80 (PM historical production strategy), the same gate the #77 production run waits on.

### PDC era-scoped backfill — #79

`python -m usa_wa_adapter_pdc.harvest_pdc`

- **The #75 crux.** #75 matches every winner cohort against the *current* roster
  (correct for "who sits now"). Historically each cohort must match the roster of the
  biennium it **seated**: election year `Y` → biennium `[Y+1, Y+2]`. Match the House +
  Senate cohorts of `Y` against **that** biennium's roster only → each cohort maps to
  exactly one roster, normalized once (this also sidesteps the runner's cache-skip,
  which would otherwise pin a cohort to whichever biennium fetched it first).
- Per election year with data (~2010→current), build the seating-biennium roster
  **archive-first** from the WSL sponsor archive (#77), archive each cohort (#54),
  emit `person_wa_pdc` + House **Position** seat observations into the span engine.
- Coverage-limited: pre-~2015 House cohorts are partial; log the shortfall.
- The #74 mid-biennium mover inference applies historically too.

### Committee membership — #82

`python -m usa_wa_adapter_legislature.harvest_committee_membership`

- **Archive-first enumeration.** The committee historical backfill (sub-project 3,
  shipped) already archived `committees-roster:<biennium>`. Read it offline to
  enumerate each biennium's committees (`Name` + agency), then fan
  `GetCommitteeMembers(biennium, agency, Name)` over them.
- Floor ~1999-00; House/Senate standing committees only. Fan-out ~40 committees ×
  ~14 biennia ≈ **~560 paced SOAP calls** — `--pause-seconds`, archive each (#54),
  closed biennia cache-hit on re-run.
- Emit membership as `committee` spans (member `Id` → committee Org via the `member`
  Role). New offline re-parser `parse_committee_members` + cassette round-trip guard.

### PM production — #80

- **Ordering:** Persons (+ identifiers) → Roles → span Assignments (FK/anchor deps).
- **Backpressure is a sidecar capability, not a one-off.** A large one-time enqueue
  won't be an unusual event (every historical backfill, every re-materialization
  produces a burst), so egress throttling belongs **in the sidecar itself** as
  first-class settings, exercised by the normal reconcile/drain — not bolted onto a
  bespoke producer CLI. See §Central throttling.
- **Subscriptions:** each produced entity → PM auto-subscribes; re-run
  `prune_subscriptions` (#73) after and confirm the mirror-set scoping holds at scale.
- Merged spans keep the Assignment count low. A `validate_*` pass (analog of
  `validate_committees`) spot-checks local↔PM for a sample of historical members.

### Central throttling (cross-cutting) — #77/#80/#82

Both the source-facing sweeps and the PM egress need pacing, and both recur beyond
this epic — so throttling is designed **centrally**, once, rather than as per-CLI
`--pause-seconds` flags and ad-hoc drain chunking:

- **WSL egress — a transport-level limiter.** A single configurable rate limiter in
  `WSLClient` (min inter-request interval / token bucket, env-tunable) that *every*
  caller routes through — the daily refresh, `harvest_sponsors` (#77), the committee
  membership sweep (#82, ~560 calls), and the existing committee harvest. Per-CLI
  `--pause-seconds` becomes an override of the central default, not the only guard, so
  a new caller can't accidentally burst against WSL by forgetting to pace itself.
- **PM egress — sidecar throttle settings.** First-class `SidecarSettings`
  (outbound request rate / inter-batch delay, alongside `OUTBOX_COMMIT_CHUNK_SIZE`)
  that bound the drain regardless of how large the outbox grows, with the
  `UNAVAILABLE` re-drive path as the backstop. The historical backfill is the forcing
  function to build this properly, since bursty production is the steady-state norm.

## Identity pre-flight — #81

Before minting ~800 Persons keyed on WSL `Id`, a write-free probe extends
`probe_member_identity` to sweep old cross-biennium pairs (1991-92↔1993-94, …) and
tally `Id` agreement by name. The committee backfill learned WSL re-keys
*committees* across eras — confirm members do **not**, or scope the floor to where
they don't. Cheap to check, expensive to get wrong.

## Testing (TDD)

- **Phase A (#77)** — fake WSL + runner: fill-only archival across a range,
  materialize-by-`Id`, dedup a member across biennia to one Person, idempotent re-run.
  Cassette round-trip for `parse_sponsors` on a **historical** biennium.
- **Span engine (#78)** — pure builder with crafted `Id` sequences: single term,
  multi-term contiguous (one span), dormancy gap (two spans), party switch mid-tenure,
  House→Senate move (two overlapping-biennium spans), open current span, rollover
  closing a span.
- **PDC (#79)** — historical House + Senate cassettes; era-matching (a 2012 Senate
  winner resolves against 2013-14, not current); a historical mover.
- **Committee membership (#82)** — cassette for `parse_committee_members`; archive-first
  enumeration; membership span across contiguous biennia.

## Sequencing

1. **#81** — validate `Id` stability at depth (blocks the mint).
2. **#77** — harvest Persons/identifiers to the floor.
3. **#78** — span engine (subsume the current path; migrate per-biennium rows).
4. **#79 + #82** — PDC House-seat spans + committee-membership spans (parallel;
   both feed the span engine).
5. **#80** — produce the cohort to PM.

## Open questions / risks

- **Data gaps read as dormancy.** A member genuinely serving but absent from one
  `GetSponsors` window would split into two spans. Rare; spot-check the emitted spans
  on a `--dry-run` before the first PM production.
- **Deep-name-match quality (#81).** Very old rosters may have inconsistent name
  formatting; the `Id`-agreement probe must fold names robustly (reuse the PDC/WSL
  folding primitives).
- **Span-key migration (#78).** Retiring the shipped per-biennium assignment rows in
  favor of span rows must be idempotent and not orphan PM-linked assignments; design
  the transition explicitly (the `pm_assignment_id` link must carry to the span row).

(PM enqueue volume and WSL sweep load were open questions in the first draft; both are
now **decided** — handled by central throttling, see §Central throttling — rather than
per-caller pacing. The heavy WSL sweep is #82 (~560 POSTs); the largest PM enqueue is
#80's cohort. Both drip through the central limiters and stay one-time / cache-bounded.)
