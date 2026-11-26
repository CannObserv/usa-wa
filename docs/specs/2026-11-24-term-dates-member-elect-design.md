# Term dates and member-elects: the 2027 rollover release (#598, #600)

**Status:** owner decisions recorded 2026-11-24 (§ Decisions); implementation next · **Issues:** #598 (statutory term dates),
#600 (member-elect role assignment) · **Builds on:** #597 (the two clocks, PR #602), #599 (the
next-biennium capture, PR #601), #501 (the boundary-basis contract, D6) · **Deadline:** live
before the term clock's next flip. Under today's rule that is 2027-01-01; under this release's
rule it is 2027-01-11.

## Intent

Every published tenure edge we don't hold a date for sits on a calendar year: Jan 1 opens a
term and Dec 31 closes it. Washington's legislators don't serve on calendar years.
RCW 44.04.021: "The regular term of office of each senator and representative shall commence
on the second Monday in January following the date of election." A 2025-26 legislator who
doesn't return serves until **2027-01-10**, not 2026-12-31. Their successor serves from
**2027-01-11**, not 2027-01-01.

Between the November general election and that second Monday, the winner is a
**member-elect**: elected but not yet seated. The owner decided (2026-11-24) that this is its
own role assignment, separate from the legislator's.

The two changes share one clock and one release. #598 sets the term start that #600's
member-elect window ends against, and both change `assignments` and `role_periods`. Power-map
gets one notice and one re-pin.

## Decisions already made (owner, 2026-11-24)

- Legislator terms map to the relevant statute. The filing window (CannObserv/cli's December
  rule, #597) drives harvests and meetings, never tenure.
- Committee `active` follows the term clock (INV1). An organization is one WSL committee id
  across biennia, so only new and retired committees flip, and only at the term start.
- A member-elect assignment runs from the race call to the day before the term starts.
  - **Who:** every winner of the seating general, re-elected incumbents included; senators
    not up this cycle are excluded.
  - **Start:** the earliest evidence (WSL's next-biennium listing or SOS certification), with
    a window from general election day. An operator attestation overrides it.
- Provisional ballot Positions are allowed before certification (#599 D2).
- The partial-roster vacancy warning stays a gate (§ D3; this supersedes #599 D3's
  "informational").

## Part 1 — the statutory term clock (#598)

### 1.1 One function owns the term

`usa_wa_common.legislative_calendar` gains the term calendar:

- `term_start(label) -> date`: the second Monday in January of the biennium's odd year.
  - 2025-26 → 2025-01-13; 2027-28 → 2027-01-11; 2029-30 → 2029-01-08.
- `term_end(label) -> date`: the day before the next term starts. 2025-26 → 2027-01-10.
- `term_biennium_on(day)` becomes the biennium whose term holds `day`: 2027-01-10 → 2025-26,
  and 2027-01-11 → 2027-28.

`clearinghouse_domain_legislative.terms.biennium_start_date` keeps meaning Jan 1, because the
committee-rename windows (#46) use it as a calendar boundary. The span engine stops calling it
for tenure. The engine is domain code (Layer 2) and can't import `usa_wa_common` (Layer 2b),
so `build_tenure_spans` takes the term calendar as a parameter (a `TermCalendar` protocol: a
`start` and an `end` per label). The pipeline passes WA's. That keeps the engine
jurisdiction-agnostic and moves the WA statute to where WA facts live.

### 1.2 What moves

**Observed edges**, the 8,085 starts and 6,826 ends published as `observed` in v20261109:

| Edge | Before | After |
|---|---|---|
| `valid_from` (observed) | Jan 1 of the start biennium | `term_start(start biennium)` |
| `valid_to` (observed) | Dec 31 of the end biennium | `term_end(end biennium)` |
| `valid_from_latest` (observed) | the start biennium's Dec 31 | `term_end(start biennium)` |
| `valid_to_earliest` (observed) | the end biennium's Jan 1 | `term_start(end biennium)` |
| `role_periods.valid_from` | a biennium's Jan 1 | `term_start` |

**The convention for `valid_to` is unchanged.** It is a span's **last day**, inclusive. Dec 31
already is one, and `term_end` is the day before the successor's `term_start`, so the occupancy
macro, `as_of` and the clip's touch rule keep their meaning.

**`is_active` keeps its definition**: open, the span reaches the current *term* biennium
(D6 invariant 1, `assignments_boundary_intervals.sql:40`). It already behaves as "serving
today": the term clock flips on the second Monday, so 2025-26 spans stay open through
2027-01-10, and the 2027-01-11 build closes them at 2027-01-10. The build still depends on the
date only through the term clock, as it does today.

**Event bucketing moves with the edges.** Every place that buckets a dated event, window or
return into a biennium for **tenure** uses the term calendar. Moving the edges alone breaks
the overlay. A seating dated 2025-01-07, before the 2025-01-13 term start, would fall
outside the 2025-26 span. A historical one goes inert, and a current one reaches `_synthesize`
and mints a duplicate key. The sites:

- **`operator_overlay`**:
  - `_span_covers`, `_matches_seat`, `_opens_after_seating` and `_seating_starts_tenure`;
  - `latest_event_biennium_by_member`, the `_in_biennium` gate, `_synthesize_closed`'s floor
    and `_split_tail`'s key;
  - mover synthesis, and the re-measured `DEPARTED_ALREADY_CLOSED_GRACE`.
- **`conformed.movers`**: the Jan 1 floors, `_house_ends`, and `_date_committee`.
- **`conformed.windows`**: `_starts_with`, `_starts_after`, `_ends_with`, and the election
  rule's ceiling.
- **`conformed.capacity`**: period floors and the tiling floor.
- **`seat_clipping._in_biennium`**.
- **`roster_pdf.projector`**: `_biennium`, and `ended_before_term`. The projector already says
  "Jan 1 approximates the mid-January session start".

**Two equality checks would stop matching without failing.** `operator_overlay.py:183` and
`movers.py:284` compare `valid_from == biennium_start_date(...)`. Both become
`== term_start(...)`. Each gets a test built **through the real builder**, so a future clock
change fails loudly.

**Calendar annotation windows stay calendar.** The roster's "Resigned January 1977" is a
month window, and the year windows in `succession.py` are calendar windows. They are what the
source states, not term edges.

### 1.3 What the data does

The year-window clauses are unchanged. Behaviour changes at the January seam:

- **Departures between Jan 1 and the term start** now close a still-open span. Example: a
  death on 2025-01-05 by a member not re-elected. Today it is logged `already_closed` against
  a 2024-12-31 end. After this release it closes the span at 2025-01-05 as `dated`. The span
  survey counts **39 dated edges** in that window in published data, including seated
  2025-01-07 (LD-48 Pos 1), seated 2017-01-03 (LD-25), and departed 1993-01-06 (LD-29 Senate).
  Each is expected to move, and the PR lists each one's before and after.
- **Seat clipping** shifts both ways. A pre-term seating now overlaps the predecessor's
  observed end and clips it (the end becomes `derived`). A pre-term exit no longer overlaps the
  successor's start, which leaves a real vacancy gap.
- `accepted_seat_overlaps.csv` (3 undated pairs) and the informational may-tier list (PM's
  cross-check) are re-derived. Stale or new pairs are reviewed, not auto-accepted.

### 1.4 How far back (owner decision, Q1)

RCW 44.04.021 dates from **1981 c 288 s 68** (formerly RCW 44.07B.870), amended 1987 c 13.
RCW 44.04.010, the session-convening statute, traces back to **1891 c 20 s 1**, and its text
today is "commencing on the second Monday of January". Sessions were biennial until Amendment
68 (1979). The statehood session convened in November 1889. Neither the 1891 nor the pre-1981
term-commencement text is readable online: the session laws are scanned images.

| Option | Effect |
|---|---|
| **A. Second Monday back to 1891** (1889-90 keeps calendar edges) | One rule for the whole archive. It rests on the 1891 session statute plus the assumption that terms began at the convening. |
| **B. Second Monday from 1981 on; calendar edges before** | Only what the cited statute states. It adds an era floor (`TermCalendar` carries it) and a visible seam in 1981. |
| **C. B now, and A after the session laws are read** | B ships by the deadline; A is a follow-up once someone obtains the 1891 and 1981 texts (State Library or OCR). |

**Decided: C** (owner, 2026-11-24). The floor is **1981**, and #604 reads the session laws to lower it. The reasoning: the deadline concerns the 2025-26 → 2027-28 seam. Back-dating 90 years
of edges on an inferred rule is the kind of wrong hop #101 calls dishonest. The floor is one
constant, and lowering it later is a reviewed data change.

### 1.5 Never-seated becomes an operator attestation (owner, 2026-11-25)

Statutory edges expose a shape that calendar edges hid. **John Lovick** (WSL 3476) was
re-elected in November 2006 and resigned on 2007-01-05, three days before the 2007-08 term
opened (2007-01-08). The roster's 2007 row reads "Resigned January 5, 2007; Elected
Snohomish County Sheriff Nov. 6, 2007". WSL lists him for 2007-08 anyway (sponsor and
committees).

- **Calendar edges:** his departure fell inside the 2007-08 spans and left 4-day phantoms.
- **Term edges:** the departure precedes them, so they would publish as full-biennium
  memberships, plus two split "return" tails.

A date-only rule can't tell him from Pike (departed 2012-12-07) or Chapman (2024-12-05). Both
departed in December and did serve the next term. The #546 curated table
(`conformed.never_seated`, three roster senators) says a fourth case makes it an operator
attestation. The owner chose to build that now.

- **Kind `never_seated`**, person-scoped and term-scoped. `effective_date` is the **first day
  of the term the member never served** (`WA_TERMS.start(B)`). That makes it a fact keyed to
  a term, not a date the sources lack: two of #546's three have no date. The CLI takes
  `--biennium B` and computes the date. A stated date must be a term start.
- **Reasons:** `resigned` (Lovick; #546 Mulligan), `refused` (the Senate refused the
  seat), `declined` (appointed, never served).
- **Effect, in every builder:** spans of the member that **start and end in B** are removed,
  before any other event applies. So a `departed` that follows can't read them as a return.
  A span that starts in B and runs past it is a later listing, and is kept: #546's
  outgrown-row rule.
- **Misses** (an attestation that removes nothing anywhere) log
  `operator_never_seated_no_span`, as departures do.
- **Schema:** `ck_operator_events_kind` and `ck_operator_events_seat_shape` admit the kind
  (an alembic migration, owner role). The raw-store provenance and supersede/retract work
  as for every event.
- **Retiring the curated table:** the three #546 entries and Lovick's are entered as
  attestations in the same deploy that removes `conformed.never_seated`, before the next
  nightly. A nightly in between would publish the senators' phantom spans, so the release
  runbook makes the entries one step.

## Part 2 — the member-elect assignment (#600)

### 2.1 Shape

| | House | Senate |
|---|---|---|
| `span_kind` | `chamber-house-elect` | `chamber-senate-elect` |
| `role_key` | `seat-elect:house:ld-N:position-P` | `seat-elect:senate:ld-N` |
| `role_type` | `state_representative_elect` | `state_senator_elect` |
| org | `usa_wa_house` | `usa_wa_senate` |
| span key biennium | the biennium seated (2027-28) | same |

- **Not `seat:`.** `assignments_seat_kinds_covered.sql` fails any `seat:%` role outside the two
  chamber kinds, and a member-elect isn't seated.
- **Not in `SINGLE_HOLDER_KINDS`.** The clip's member-axis pass treats one member in two
  single-holder seats as a chamber move. It would clip every re-elected incumbent, who holds
  `chamber-house` and `chamber-house-elect` at once. Capacity is 1 per seat-elect role, through
  its own predicate.
- **Not in `SEAT_KINDS`.** No operator `seated`/`vacated` names it (§ 2.4).
- `role_type` and `span_kind` aren't enum-gated in the datapackage, only by dbt
  `accepted_values` (`schema.yml`). So adding the values doesn't change `contract_hash`, and
  `test_only_the_new_vocabularies_declare_an_enum` stays green. The FieldDoc prose and
  `docs/API.md` list the new values.

### 2.2 Dates and basis

The owner's rule was "observed, with a window from general election day". In the D6 contract,
an uncertain start is `[valid_from, valid_from_latest]`, and `observed` means "on a term
edge". So the rule is published the way the roster's "an election opens a start" precedent
already publishes one (`conformed.windows`):

- `valid_from` = **general election day** (2026-11-03), `start_basis` = `dated`, and
  `valid_from_latest` = **the first evidence**. The true start, the call, lies in that window.
- `valid_to` = `term_end(the biennium before)` (2027-01-10), `end_basis` = `dated`: the
  statute states it.
- `is_active` = **false** throughout, because `valid_to` is set (invariant 1). A member-elect
  isn't serving. Consumers ask "who is elected now" with `as_of`, not `is_active`. API.md says so.
- `assignments_boundary_intervals.sql` gains one clause: an elect kind's dated start may carry
  a window ending on its first-evidence date.

Hillary Pham (#595, WSL 35476) has no WSL evidence in the prod raw store before the
2026-11-25 nightly, because the next-biennium capture started then. Her record is
`[2026-11-03, 2026-11-25]` → 2027-01-10. The probe's 11-24 count names no members, so it
isn't evidence for her. If 11-24 matters, it's an operator attestation (§ 2.4).

### 2.3 Who, and the evidence

**Winners** come from the seating general's ballot (`stg_sos_results`, the even year before
the biennium):

- **House:** `build_house_winners` per (LD, Position). **Senate:** `build_senate_winners` per
  LD, and only LDs whose Senate seat was on that ballot. That excludes holdovers by
  construction: WSL lists all 49 senators, but only the ballot says who was elected.
- **The never-guess rule holds.** A tie, a missing count or an ambiguous name match drops the
  seat and logs it, same as `build_senate_winners` does today.
- **Ballot to person:** a winner joins the **next-biennium** WSL sponsor by `(LD, folded
  surname, party)`, the same `position_for` key the House seat already uses. A winner WSL
  hasn't listed has **no person** yet: PDC and roster keys are pair-only, and the registrar
  mints from WSL ids. That winner gets no member-elect until WSL lists them. Their window
  start is still election day, so a late listing just widens `valid_from_latest`.

**First evidence** is the earliest of:

1. **WSL's first listing:** the earliest `fetched_at` of a `sponsors:<next>` wire carrying the
   member. A new staging model, `stg_wsl_first_seen`, walks the raw store's manifests (staging
   is latest-wire only today), parses each distinct sha once, and keeps the earliest
   `fetched_at` per `(biennium, member_id)`. It's needed because byte-identical refetches are
   recorded nightly.
2. **SOS certification:** the results platform's election JSON carries
   **`isOfficialResults`** (`false` on 2026-11-24, `lastUpdated` 2026-11-24T20:40Z). The SOS
   harvest already fetches that JSON to find the workbook. It starts archiving it as
   `sos-election:<YYYYMMDD>`, and the first manifest entry where the flag is `true` is
   certification day for every race on the ballot.
   - The 2008-2025 CSV path has no such flag. Member-elects before 2026 are out of scope (§ 2.5).

Citations: the WSL sponsor wire that evidenced the start (key biennium 2027-28, so the
existing assignment rule cites `sponsors:2027-28` unchanged), and the `sos-election:` wire when
certification is the earliest evidence. The `without_future_bienniums` docstring claim "no
assignment cites one" is updated. **As built (#612):** only the sponsor wire; the
`sos-election:` citation is #611 (for 2026, WSL's first listing precedes every archived
official wire, so certification never binds).

### 2.4 Operator overrides

A dated call or concession, or "declined the seat", is attested with the existing `seated` /
`vacated` events on the elect kind, using new **reasons** (`called`, `conceded`, `declined`).
Reasons have no DB CHECK, so **no migration**. That means adding the elect kinds to the
overlay's matchable kinds without adding them to `SEAT_KINDS` for vacancy logic. The overlay's
`_synthesize` must not mint an open span for an elect kind, since an elect span always has its
statutory end. A `departed` (death) closes a member-elect span like any other.

**As built (#612):** deferred to #611, except a `departed` inside the window, which ends a
member-elect early (`conformed.elect`). No 2026 case needs the `called` / `conceded` /
`declined` reasons yet.

### 2.5 Scope in time

Member-elects are built for **the current term flip forward**: the 2026 general seating
2027-28, and every general after. Earlier ballots have no first-evidence wires (no WSL
next-biennium capture, no certification flag), so a historical member-elect would be
election day to `term_end` with nothing in between. That's a fabricated window. Odd-year
specials fill unexpired terms from certification, not January. They're excluded until the
first one occurs and its shape can be audited (there's one each November; the next is 2027).

### 2.6 Gates

- New: `assignments_elect_one_per_seat`: at most one member-elect per seat-elect role per
  biennium.
- New: `assignments_elect_winners_covered` (warn): every ballot winner WSL lists has a
  member-elect span.
- New: `assignments_elect_not_holdover` (error): no Senate member-elect for an LD whose seat
  wasn't on the ballot.
- `chamber_vacancy`, `chamber_excess`, `one_seat_per_member`, the occupancy macros and INV1
  filter by explicit kinds or `is_active`, so member-elects are excluded from all of them.

**As built (#612):** `assignments_elect_winners_covered` is a count, `member_elects` in the
`registry_coverage` report (`won`, `built`, `unlisted`, `ambiguous`, `no_evidence`), not a
dbt warn: WSL lists winners for weeks after the general, and with no accepted warnings a
warn would email nightly until January. `assignments_elect_not_holdover` checks the House
too.

## D3 — the partial-roster vacancy warning

Under the term clock, nothing counts 2027-28 seats until 2027-01-11. By then WSL lists every
sworn member, and the House Positions come from the certified ballot. A partial roster after
the term start is a real data gap. **Decided (owner): `assignments_chamber_vacancy` stays a
gate, with no grace window.** A vacancy after the term start emails. This supersedes #599 D3.

## The release

| Dataset | Now | After | Why |
|---|---|---|---|
| `assignments` | 1.8.0 | **1.9.0** | Same shape and enums. Observed edges are term edges (semantics). Adds member-elect kinds. |
| `role_periods` | 1.0.0 | **1.1.0** | Period floors are term starts. Adds seat-elect roles. |
| `roles` | 1.3.0 | **1.4.0** | Adds two `role_type` values (seat-elect roles). |
| `stg_sos_election` | — | **1.0.0** (new) | The archived election JSON (certification evidence). |
| `stg_wsl_first_seen` | — | **1.0.0** (new) | First-listing evidence. |

No `contract_hash` moves on the existing datasets. So by the gate it is a minor,
semantics-only release, the precedent of organizations 1.2.0. **Decided (owner): minor.**
The power-map notice announces it without asking. Nearly every `assignments` and
`role_periods` row changes in one mint.

**Sequence**, as #501 did it:
1. PR A (spec) merged.
2. PR B: the term clock and #598 behind the gate, tests red then green.
3. PR C: member-elect.
4. Write-free what-if builds: one with today's date, and one pinned to 2027-01-11, each
   diffed against prod. The 39 January edges and every changed overlap pair are listed.
5. Power-map notice with versions, hashes, row deltas and the release date, at least a week
   ahead.
6. One deploy before **2026-12-31**, so the Jan 1 nightly runs on the statutory clock and no
   span is rewritten twice.

## Decisions (owner, 2026-11-24)

| | Question | Decision |
|---|---|---|
| Q1 | History floor | **C:** second Monday from 1981, calendar edges before. #604 lowers the floor once the 1891 and 1981 session laws are read. |
| Q2 | Version | **Minor:** `assignments` 1.9.0, `role_periods` 1.1.0, `roles` 1.4.0. Announced to power-map, not asked. |
| Q3 | D3 | **Always a gate**, with no grace window (supersedes #599 D3). |
| Q4 | Member-elect scope | **2026 general forward.** Odd-year specials wait for the first one (Nov 2027) to be audited (#605). |
