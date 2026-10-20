# Release 2.1.0: seat model, boundary basis and intervals, own names

- **Date:** 2026-10-17
- **Status:** owner decisions recorded 2026-10-17 (Q1–Q6, all as recommended; § Owner decisions).
  Waiting on power-map review of the shape (§ Effect on power-map). Nothing here is built.
- **Issues:** usa-wa#501 (this release), #384, #369, #362 (absorbs #230 and #479 as shapes), #360
  (increment 2). Decisions: #384, #369, #362 and #360, each the 2026-10-13 owner comment linked from
  #501.
- **Corrections to the brief:** posted before this design, in
  [#501 comment 6227127207](https://github.com/CannObserv/usa-wa/issues/501#issuecomment-6227127207)
  (corrections 1–8) and
  [comment 6227153239](https://github.com/CannObserv/usa-wa/issues/501#issuecomment-6227153239)
  (correction 9: three substitutes, not two).
- **Templates:** [`2026-09-02-dataset-publication-replatform-design.md`](2026-09-02-dataset-publication-replatform-design.md)
  (contract and serving shape) and #447's `org_lineage` (a dataset added beside existing ones).

Measurements are from `main` @ `4e933c62`, the 2026-10-16 nightly build (`data/pipeline.duckdb`), its
catalog (`checked_at` 2026-10-16T08:05Z), and `registry.operator_events`, unless a line says
otherwise.

## Goal

One additive release, named "2.1.0", that carries four decided changes in a single lockstep deploy:

| Decision | Adds |
|---|---|
| #384 | `persons.name_own`: a person's own name where the roster prints a married-name form |
| #369 | `assignments.start_basis`, `end_basis`: where each boundary date came from |
| #360 inc. 2 | `assignments.valid_from_latest`, `valid_to_earliest`: how precisely each boundary is known |
| #362 | a general seat model: per-period seat capacity, `assignments.membership_type`, and one occupancy predicate shared with power-map |

**`role_key`s don't change.** Every change is additive, so every version move is a minor or a new
dataset.

## The release at a glance

| Dataset | Today | After | Change |
|---|---|---|---|
| `persons` | 2.0.0 | **2.1.0** | `+ name_own` |
| `assignments` | 1.7.0 | **1.8.0** | `+ start_basis, end_basis, valid_from_latest, valid_to_earliest, membership_type` |
| `role_periods` | none | **1.0.0** | a new conformed dataset: per-role capacity periods (D1) |
| `roles` | 1.3.0 | **1.3.0** | no change: the seat label already ships as `qualifier` (D2) |

This differs from #501's body, which expects `roles` → 1.4.0. That expectation assumed capacity and
plan become `roles` columns. They can't (D1), and the label is already there (D2).

## Decisions

### D1. Capacity lives in a new dataset, `role_periods`

`roles` has no time axis. ONTOLOGY.md § 1 calls a role "a template, not an occupancy", and a role
row is one per `role_key`. Capacity, though, varies over time on one key: `seat:senate:ld-19` held
5 in 1889–90 and 1 from 1891 [measured: `stg_roster_members`]. The districting plan varies the same
way. These options were weighed:

| Option | Verdict |
|---|---|
| **A new dataset of capacity periods** | **Chosen.** Additive. Keeps `roles` a template. The time axis is explicit, so a consumer joins on `role_key` plus an instant. Generalises to US Senate classes, subdistricts and ex-officio seats without touching `roles`. |
| Periodised `roles` rows | Rejected. `role_key` stops being unique in `roles`, which is a key change for every consumer of the dimension: a major (`roles` 2.0.0), which the owner's constraint rules out. |
| Carry capacity on `assignments` | Rejected. A tenure can cross a capacity or plan change under one span (spans split on the discriminator, not on the plan), so one value per row is lossy. Two overlapping rows could disagree about the seat they share, and the occupancy predicate needs a property of the *seat* at an instant, not of a tenure. |

**Shape.** `role_periods`, conformed tier, key `(role_key, valid_from)`:

| Column | Type | Null | Meaning |
|---|---|---|---|
| `role_key` | string | never | the role this period describes; joins `roles.role_key` |
| `valid_from` | date | never | first day of the period: a biennium floor |
| `valid_to` | date | open period only | last day, **inclusive**: a biennium ceiling; `null` = through the present |
| `max_holders` | integer | unlimited roles | the most principals the role seats at once; `null` = no limit |
| `max_holders_basis` | string | never | `kind`: the seat kind's rule (one per Senate seat or House Position; no limit for party or committee membership). `roster`: counted from the roster's bare multi-row listing |

**Two date conventions, stated in each field's description** (power-map review, seq 3). A period's
`valid_to` is the last day *inside* it, and periods tile with `next.valid_from = prev.valid_to + 1 day`
(Dec 31 → Jan 1), so `t` is in a period iff `valid_from ≤ t ≤ coalesce(valid_to, ∞)`. An assignment's
`valid_to` is a **handoff date**: a dated successor may open on that same day, so the occupancy
predicate (D7) reads it as exclusive. The two differ on purpose, and the descriptions say so.

**Dense, across every role.** Each role's periods tile from the floor of its earliest published
tenure to an open last period, with no gaps or overlaps. Party and committee roles get one open
period with `max_holders = null`. That way the occupancy predicate is one rule, "where `max_holders`
is not null", with no list of kinds for a consumer to keep in step with ours. This is the step that
lets PM retire the type-level `occupancy` (D7). Expected first publish: 312 roles + 5 bare 1889
periods = **317 rows**. `min_rows` = 300 (the #472 rule: about 95%, rounded down).

**Derivation.** A new pure module, `usa_wa_pipeline.conformed.capacity`, moves today's SQL rule
into Python unchanged: a Senate district-year the roster lists with more than one row and **no
annotation** seats that many principals for that biennium; any annotation in the district-year means
capacity 1. This is conservative: ambiguity resolves toward policing, which is the gate's existing
reading. Every other chamber-seat period is `max_holders = 1`, `basis = kind`. Today exactly five
district-years qualify, all in 1889: LD-2 ×3, LD-5 ×3, LD-8 ×2, LD-18 ×3, LD-19 ×5.

**Served: no** (Q5, decided). `org_lineage` is published and unserved. `verify_contract` binds
only `SERVING_TABLES`, so a publish-only dataset adds no lockstep obligation.

### D2. The seat label is `roles.qualifier`; no new column

#362 asks for "an optional seat label (Position 1, A, Class II; never invented where the source has
none)". `roles.qualifier` already is one: it's `Position 1|2` on all 98 House seats and null on all 49
Senate seats [measured], and it's part of the key. A second column would duplicate it. The build
only needs to document `qualifier` as the seat label in the `roles` datapackage description, which
moves no `contract_hash` (D8).

### D3. The districting plan is part of the model, but its column waits for a source

A period boundary is wherever capacity *or* plan changes, so `role_periods` is the plan's home.
Nothing populates a plan today: WA's plan years need a source audit (ARCHITECTURE.md's first rule),
and the roster's district numbering can't be read as one. **Recommendation:** add `district_plan`
to `role_periods` (1.1.0, additive) in the release that first populates it, rather than publishing an
all-null column now (Q3). The key structure already allows it: a plan change splits a period.

### D4. Clipping reads capacity and counts principals only

`clearinghouse_domain_legislative.seat_clipping` stays jurisdiction-agnostic (Layer 2). Its inputs
grow, and it gains no new imports:

- `clip_seat_counterparts(spans, *, capacity=None)` and `clip_seat_families(..., *, capacity=None)`.
  `capacity` maps `(kind, discriminator)` to a sequence of `CapacityPeriod(valid_from, valid_to,
  max_holders)`, a new frozen dataclass in Layer 2. With it absent, or a seat missing from it, every
  `SINGLE_HOLDER_KINDS` seat has capacity 1, which is today's behaviour. So existing callers and
  tests are unchanged.
- **A pair whose overlap touches a period with `max_holders > 1` is declined**, with a new reason,
  `multi_holder_period`. With more than one lawful holder, geometry can't say which pair is a
  succession, so the clip refuses rather than guesses. The occupancy gate (D7) then judges the seat
  by count. This closes #362's latent bug by construction: a dated 1889 annotation can no longer
  rewrite co-senators into a succession.
- **Non-principal spans are invisible to the seat axis.** A substitute is never a counterpart, and
  its principal is never clipped by it. The member axis (#282) is unchanged.
- **"Stated" reads the span's basis (D6), not its geometry.** `_stated_start` / `_stated_exit` test
  `start_basis` / `end_basis == dated`. This fixes correction 5: Woodall's seating, dated 1957-01-01,
  now counts as stated.

**Layering.** `CapacityPeriod` is Layer 2. The WA derivation (D1) is Layer 3c and reads only staging
rows, so import-linter's contracts are untouched: `usa-wa-pipeline` already imports the domain, and
the domain imports nothing new. `assignment_rows` gains an optional `capacity` argument, and the
`assignments` dbt model builds it from `dbt.ref("stg_roster_members")`, which the model already
reads. The same capacity periods feed `role_periods`, so the clip and the gate read one statement.

### D5. `membership_type`, and what writes it

**Values:** `principal | substitute | ex_officio | acting`, a closed enum, non-null on every
assignment. `principal` means holding the role in one's own right; it's the value for every party
and committee row too. Only `principal` counts toward `max_holders`.

**Populated in 2.1.0:** three rows are `substitute`. All are military substitutes, and the roster
states both halves of each [measured: `stg_roster_members`]:

| Seat | Substitute | Principal on leave | Where the roster says so |
|---|---|---|---|
| LD-22, 1991-01-14 → 04-18 | Lela Kreidler | Myron "Mike" Kreidler | her annotation; **his name field** ("On leave of absence for military duty Jan. 8, 1991 to April 18, 1991") |
| LD-6, 2005-04-14 → 04-16 | Jon Wyss | Brad Benson | both annotations |
| LD-20, 2017-07-18 → 07-23 | Marlo Braun | John Braun | her annotation; #144 adjudicated GENUINE |

`ex_officio` and `acting` are declared with zero rows. #479 populates the first.

**Writer: a curated table**, `SUBSTITUTE_TENURES` in a new `usa_wa_pipeline.conformed.memberships`,
following the `DISTINCT_NAMESAKES` / `JOIN_ADJUDICATIONS` idiom. Each entry is keyed by the span's
`(source, member_id, span_kind, span_discriminator, span_start_biennium)`, which is stable across
rebuilds. Each carries its evidence: both roster clauses verbatim and the edition. It's applied in
`build_families`, after the overlay and before the clip, so every caller sees the same spans
(`assignments`, `registry_coverage`). Guards, as unit tests over the built corpus:

- every entry matches exactly one built span (no inert entries);
- that span lies inside a principal's span on the same seat;
- both of its edges are `dated`.

Why not the alternatives:

- **A `seated` reason (`substitute`).** ONTOLOGY.md § 3 says reasons are "evidence
  classification, not behaviour". This changes behaviour (the clip and the gate), so by the repo's
  own rule it isn't a reason.
- **A new event kind.** § 5 reserves new kinds for a new *effect on spans*. A substitution moves no
  boundary.
- **Parsing the roster.** There are three cases, and the leave clause appears in two different
  fields (the annotation, and a parenthetical in the name). Keying a parser on that wording breaks
  ARCHITECTURE.md's second rule.

**Revisit when** a fourth case appears, or when a source publishes substitutions. Then this becomes
an operator attestation (an `operator_events`-style table, with CLI and raw-store provenance), not
a longer list.

### D6. Boundary basis and interval, defined together (#369 + #360)

The basis says **where** a boundary date came from. The interval says **how precisely** it is known.
They are separate axes, linked by the invariants below.

**One closed enum for both columns**, `boundary_basis`:

| Value | Means | Start | End |
|---|---|---|---|
| `dated` | a source states this tenure's date: an operator event (day), or a parsed roster annotation window (month or year, D6a) | ✓ | ✓ |
| `derived` | a rule moved this edge onto another tenure's boundary. It's an inference, not evidence (`39749e1`) | ✓ | ✓ |
| `observed` | the biennium edge of the first or last attestation, and nothing narrower | ✓ | ✓ |
| `open` | the tenure is still running (`valid_to` is null) | ✗ | ✓ |

**Who sets it.** `TenureSpan` gains `start_basis` and `end_basis` (Layer 2 constants beside
`span_kinds`). Every boundary writer sets them; none is inferred from dates (correction 5):

| Writer | Start | End |
|---|---|---|
| `build_tenure_spans` | `observed` | `observed`, or `open` when it reaches the current biennium |
| overlay `seated` (re-date, `_opens_after_seating`, synthesize) | `dated` | unchanged; `open` on a synthesized span |
| overlay `vacated` / `departed` (`_close`) | unchanged | `dated` |
| overlay `_synthesize_closed` (a #145 mover) | `observed` (the biennium floor) | `dated` |
| overlay `departed` split tail (#267) | `derived` (the return date is another span's start) | unchanged |
| `merge_party_continuity` (#289) | the earliest span's | the latest span's |
| `date_mover_committees` (#490) | `derived` | `derived` |
| `seat_clipping`, both axes (#360, #282) | `derived` | `derived` |
| annotation window (D6a) | `dated` | `dated` |

A unit test asserts consistency between basis and geometry over the built corpus: `observed` ⇒ on
its own biennium edge, and `open` ⇔ `valid_to is null`. The reverse isn't asserted, because a dated
edge may land on a biennium edge (11 of 533 events do [measured]). A writer that moves an edge and
forgets its basis fails this test unless it moves the edge exactly onto a biennium edge, which is the
one residual blind spot.

**Intervals.** `[valid_from, valid_from_latest]` contains the true start, and
`[valid_to_earliest, valid_to]` the true end. Both are dense: an exact edge has equal bounds (the #360
design). `F(b)` and `C(b)` are a biennium's first and last days:

| Basis | Start: `valid_from` … `valid_from_latest` | End: `valid_to_earliest` … `valid_to` |
|---|---|---|
| `dated`, day | `d` … `d` | `d` … `d` |
| `dated`, window `[w₀, w₁]` | `w₀` … `w₁` | `w₀` … `w₁` |
| `observed` | `F(start)` … `min(C(start), valid_to)` | `max(F(end), valid_from)` … `C(end)` |
| `derived` | `x` … `max(x, min(C(start), valid_to))` | `min(x, max(F(end), valid_from))` … `x` |
| `open` | n/a | `null` … `null` |

For `derived`, `x` is the moved date: true start ≥ `x` (one holder at a time), still within the
attested start biennium. For an end it mirrors: true end ≤ `x`.

**Invariants.** The build adds these as dbt tests on `assignments`:

1. `end_basis = 'open'` ⇔ `valid_to is null` ⇔ `valid_to_earliest is null` ⇔ `is_active`.
2. `start_basis ≠ 'open'`; both columns lie in the enum.
3. `valid_from ≤ valid_from_latest`; `valid_to_earliest ≤ valid_to`; `valid_from ≤ valid_to_earliest`;
   `valid_from_latest ≤ valid_to` (on a closed span).
4. `observed` ⇒ the published bound is the attested biennium's edge, and the inner bound is the table's.
5. `dated` ⇒ degenerate, or the inner bound closes a calendar month or year opened by the outer one.
   The interval's width *is* the precision; no separate precision column.
6. `derived` ⇒ never degenerate unless clamped by rule 3. An inference doesn't pose as exact.

**#369's three settle-points:**

- **Clipped boundaries** are `derived`, never `dated`.
- **Term-start seatings are `observed` in 2.1.0.** The alternative was a fifth value, `term` ("the
  roster lists the member with no mid-term annotation, so the tenure began at the convening"),
  which would make most chamber edges exact. It is not recommended now:
  - the absence of an annotation is the only evidence;
  - the WSL family's observations don't carry the roster row they came from;
  - and on that family absence isn't evidence at all, since the sponsor wire omits mid-biennium
    appointees from their appointment biennium (correction 7).

  Adding `term` later is an enum extension, a minor (Q4).
- **The value set** is the closed four above, documented in the datapackage (D8).

**#360's question.** Can an `observed` end ever have `valid_to_earliest < valid_to`? **It always
does.** An observed end means "last attested in this biennium", so its interval is that biennium,
clamped. Only a day-dated end is degenerate.

### D6a. Annotation windows: increment 2 wired in

`ParsedDate.window` (#360 `70e9301`) bounds 132 month- or year-precision clauses that nothing reads
today. In 2.1.0 a Layer 3c pass turns each into an edge window for the member's span on that seat.
It runs after the overlay and before the clip. An `observed` edge with a window becomes `dated` with
that window: the outer bound **moves** to the window's edge, and the inner bound is the window's
other edge. The rule is "own evidence beats inference": an edge the window dates is no longer
clippable.

Rasmussen (LD-29) shows why the outer bound has to move. Today the clip publishes his start as
1971-08-09, a derived date. Under the window it becomes `dated` [1971-10-01, 1971-10-31], and the
overlap with McCutcheon's 1971-08-09 exit disappears rather than downgrading to "may" (correction 4).

- An operator event that already dates the edge **wins**. If its date falls outside the window, the
  pass counts and logs the contradiction; it never moves a dated edge.
- A window in the biennium **before** the span's start follows `_opens_after_seating`'s one-biennium
  rule: `span_start_biennium`, and so the key, is unchanged.

Windows move values, not the contract. If the build has to be split, the columns ship first and
windows land after, with no version bump (Q6).

### D7. One occupancy predicate, shared with power-map

For a role `R` and an instant `t` inside a period of `R` with `max_holders = m ≠ null`:

```
|{ a ∈ assignments : a.role_key = R, a.membership_type = 'principal',
                     a.valid_from ≤ t < coalesce(a.valid_to, ∞) }|  ≤  m
```

Intervals are half-open, so a same-day handoff is not an overlap. That equals our current
`is distinct from` rule and PM's strict `<` (#489). The maximum is reached at some principal's start,
so the check runs at each start.

**Two tiers**, from D6:

| Tier | Interval per principal | Gate severity |
|---|---|---|
| **certain** | `[valid_from_latest, valid_to_earliest)`, empty when latest ≥ earliest | `error`, except pairs on #360 part A's named accepted list |
| **may** | `[valid_from, valid_to)`, today's predicate | `warn` |

`assignments_seat_occupancy.sql` is rewritten on this predicate. It reads `role_periods` and
`membership_type`, and **its `stg_roster_members` exclusion retires**: the capacity it encoded now
lives in `role_periods`, which the clip reads too. The roster's annotation split remains the only
source of the 1889 counts, through D1.

**What PM reads to apply the same predicate:** `role_periods` (`role_key`, `valid_from`, `valid_to`,
`max_holders`) and `assignments` (`role_key`, `membership_type`, `valid_from`, `valid_to`, plus the two
interval columns for the tiers). PM **keeps** `role_types.occupancy` as its jurisdiction-wide
vocabulary and composes the two: capacity at `t` is `role_periods.max_holders` where a period covers
`t`, else the type default (`single` → 1, `shared` → no limit). That fallback also covers PM's legacy
and admin-edited rows outside our periods (accepted limit 4). No list of kinds has to stay in step
on either side (power-map review, seq 3).

**PM applies the may tier only**: it stores outer bounds and nothing else. So the like-for-like
cross-check is our **may-tier pair list** against PM's, and the may-tier test must **list its pairs**
(span_key pairs, through dbt `store_failures`), not just count them. PM admin overlays (Robinson's
truncation, power-map#639) can make the sets differ legitimately.

**Expected movement** in the 35-pair residue (#489's confirmed set):

- The 3 substitutes (pairs #8, #9, #33) leave both sides' counts.
- The 20 multi-member pairs leave PM's count; ours already excludes them.
- Explicit basis lets the clip see edge-dated events: pair #3, Flanagan/Woodall, resolves.
- A geometric estimate splits the 35 into ≤ 27 certain and ≥ 8 may-only. Basis is approximated
  there by "on a biennium edge ⇒ observed", so `derived` starts count as exact and 27 is an upper
  bound. The build measures it properly.

### D8. Publisher: describe fields and declare enums

Published fields carry `{name, type}` only today, and `contract_fingerprint` hashes name and type
(correction 6). The build adds:

- Frictionless `description` on every field of the touched datasets (`persons`, `assignments`,
  `roles`, `role_periods`). This is prose and stays out of the fingerprint, so `roles` keeps its
  hash.
- `constraints.enum` on `start_basis`, `end_basis`, `membership_type` and `max_holders_basis`.
  `constraints` enters the fingerprint **only when present**, so no existing dataset's
  `contract_hash` moves, and a later enum change does move it, as a contract change should.
- A unit test that each declared enum equals the Python constant it came from and the column's dbt
  `accepted_values`. One vocabulary, three places, pinned together.

### D9. `persons.name_own` (#384)

A curated table, `OWN_NAMES` in a new `usa_wa_pipeline.conformed.own_names`. It has 11 entries keyed
by the roster natural key (`usa_wa_legislature_roster:<fold>:<year>`), not by the printed string.
Each records the printed form, the own name (or `None`), its class (A, B or C), and evidence. Values
are as decided: 7 class A extracted (`Nettie Luella Smith` per the 2026-10-13 call, citing the
legislature's own page), 2 class C with the honorific dropped, and 2 class B null. `name_own` is
null on every other row. Guards:

- each entry resolves to exactly one live person whose `name_full` equals the recorded printed form,
  so a reprinted edition fails loudly;
- `name_own` is non-null for exactly the 9.

`name_full`, `identity_fold` and the registry keys are untouched (#378's separation).

## Population in 2.1.0

This follows the brief's recommendation, confirmed by the owner 2026-10-17 (Q1). **Every shape is
modelled, but only these are populated:**

| Shape | Populated | Rows |
|---|---|---|
| capacity (`role_periods`) | dense for all 312 roles; `roster` basis for the 1889 Senate | 317 |
| `membership_type = substitute` | Kreidler 1991, Wyss 2005, M. Braun 2017 | 3 |
| `name_own` | classes A and C | 9 |
| basis and intervals | every assignment | ~8,400 |
| district plan | none (D3) | 0 |
| pre-1965 at-large House (#230) | none: no `chamber-house` span starts before 2001-01-01 [measured] | 0 |
| Lt. Governor `ex_officio` (#479) | none: needs new roles and a source | 0 |

## Release mechanics (phase 3, for the build thread)

- **One lockstep deploy.** Datapackage fields and `serving.*` columns land together for `persons`
  and `assignments` (`verify_contract` refuses the whole serving load otherwise). `role_periods`
  joins `PUBLISHED_DATASETS` with its first `ContractRelease` and `min_rows=300`; it is unserved
  per Q5.
- **Carry-forward.** Each dataset gets its new fields only when it next mints, so the catalog can
  legitimately mix versions for one night. All three mint on the release night, because their
  contracts change.
- **API.** Per Q5, expose `name_own` on `PersonSummary` and the five assignment
  columns on `AssignmentSummary`, with `docs/API.md` notes. **Measure API.md's token budget before
  editing.** Do the same for `ONTOLOGY.md` (§ 1 vocabularies, § 2 basis, and § 5 step 4, which gains
  `membership_type` as the precedent for a closed enum column) and `PIPELINE-PUBLICATION.md` (D8).
  `DEPLOYMENT.md` sits at about 9,920 / 10,000 and needs no edit here.
- **Announce the release date to power-map ahead of time.** Both `persons` and `assignments` move
  `contract_hash`, so PM's puller refuses them until a one-line re-pin PR merges. That stops every PM
  apply (#535 inputs-behind) and turns their unit red. PM can't pre-pin, because a pin to the new hash
  refuses today's versions. So they keep the re-pin PR ready, and fill it from the refusal log line,
  which carries both hashes. Their merge is gated on their operator. PM's count stays 55 on release
  night until its side ships (power-map review, seq 3).
- **Gates, red first (TDD):**
  - the D6 invariants and the D1 tiling tests: key, contiguity, one open period per role,
    `max_holders ≥ 1`;
  - the D5 and D9 curated-table guards and the D8 enum pin;
  - the rewritten two-tier occupancy gate;
  - `test_every_declared_contract_matches_the_build`, which goes red by design and greens with the
    three new releases.

## Effect on power-map

`roles` is PM's matching surface and is **unchanged**: the same 312 keys and the same columns. PM
gains one subscription (`role_periods`), five `assignments` columns and one `persons` column. Its
ratchet loses 23 pairs (20 multi-member, 3 substitutes): **55 → 32** once PM's side ships, and lower
after #360 part A and the basis-aware clip. PM's baseline is 55, not #489's 56: power-map#639
(Robinson) landed 2026-10-11 [PM claim, seq 3].

**Reviewed live 2026-10-17** by the power-map agent (Mayfly; answers at server ts
2026-10-17T00:34:05Z, recorded on #501). The shape is accepted. PM-side work (pin `role_periods`, a
capacity table, `membership_type` on `role_assignments`, the audit on D7 with the type-default
fallback, the release-night re-pin) goes on one power-map issue that their agent files, gated on their
operator.

## Accepted limits

1. **Intervals are relative to what we attest, not guaranteed bounds.** An undated WSL-era appointee's
   floor can sit after the true start (correction 7).
2. **The model's calendar.** Jan 1 and Dec 31 stand for the term boundaries. The session convenes on
   the second Monday of January (`operator_overlay.DEPARTED_ALREADY_CLOSED_GRACE`), so a real term
   boundary sits up to about two weeks outside the published interval. This is a documented
   convention, not imprecision the intervals claim to cover.
3. **Observed intervals are wide** (the whole attested biennium). The certain tier fires only on
   multi-biennium overlaps or dated edges, and single-biennium undated successions are warnings,
   which is honest about what we know.
4. **A role's first period starts at its earliest published tenure**, not when the district was
   created. No capacity is asserted before we publish a holder.
5. **One `role_key` still spans every districting plan** (LD-19 in 1889 is not LD-19 today).
   That's unchanged here; `district_plan` is how it's eventually told apart (D3).
6. **Substitutes are hand-curated.** A new one needs a PR until the revisit trigger in D5.
7. **The capacity rule reads annotations only.** A tenure note printed in the name field, as with
   Kreidler, doesn't count as an annotation. That's harmless today, because no multi-row Senate
   district-year outside 1889 is bare [measured], but the build should treat a
   `strip_tenure_notes` parenthetical as an annotation.

## Owner decisions (2026-10-17)

| # | Question | Decided |
|---|---|---|
| Q1 | Population scope: every shape modelled; populate only the 1889 capacity, the substitutes and `name_own`; #230 and #479 as their own children | **agreed:** adopt (brief's recommendation; now 3 substitutes) |
| Q2 | Capacity in a new dataset `role_periods` rather than on `roles` or `assignments` (D1); the name | **agreed:** approve `role_periods` |
| Q3 | `district_plan`: an all-null column now, or the column with its first population | **agreed:** with its first population (D3) |
| Q4 | Term-start seatings: `observed` now, or a fifth value `term` | **agreed:** `observed`; `term` later as a minor (D6) |
| Q5 | Serve `role_periods`? Expose the new columns in `/api/v1`? | **agreed:** `role_periods` publish-only; expose `name_own` and the five assignment columns |
| Q6 | Annotation windows in the 2.1.0 deploy, or right after it (no contract change either way) | **agreed:** in it, unless the build PR is too large to review |

## Notes for later issues

**For #360 part A (named-pair adjudication):**
- Adjudicate **32** pairs: the 35 minus the 3 substitutes (#8, #9, #33).
- Key each entry by its `span_key` pair, so entries survive re-dating.
- Expect the 2.1.0 build to move the set further: the clip will see edge-dated events (pair #3, and
  others among the 11), and annotation windows (D6a).
- Classify each pair by tier (D7): only the certain tier will error.

**For #230 (pre-1965 at-large House):**
- Capacity there is per district per plan, not a flat 2: realised counts run 1–6 across 1,819
  district-years (#230's own measurement).
- The at-large roles (`seat:house:ld-N:at-large`, a new `role_type`) are new `roles` rows plus their
  `role_periods`. The new `role_type` is a vocabulary change on `roles`, so a minor.
- `max_holders` there probably wants a third `max_holders_basis` value for apportionment (statutory),
  which needs a source.

**For #479 (Lt. Governor):** `membership_type = ex_officio` is already declared. The release needs
the new roles (President of the Senate; the Senate Rules chair), a source for the office's tenures,
and a decision on whether chairs are roles or `membership_type` values. Probably roles: a chair is a
different slot, not a different way of holding the member slot.

**For #489 / power-map:** after 2.1.0 and PM's side, PM's ratchet should read 32 before part A's
drain. Compare our **may-tier pair list** with theirs (D7). A pair on one side only means the
predicates diverged, or a PM admin overlay differs.

**For #501 phase 3 (the build):**
- Annotation windows (D6a) are the largest single item.
- Basis threading touches every overlay writer (D6 table).
- Run `lint-imports` after the Layer 2 changes.
- Re-measure the 35 before and after.
