# Plan: term dates (#598) and member-elects (#600)

Spec: [`docs/specs/2026-11-24-term-dates-member-elect-design.md`](../specs/2026-11-24-term-dates-member-elect-design.md)
(PR #603; owner decisions recorded). Branch `598-term-clock`, stacked on the spec branch.
**Deadline:** deployed before 2026-12-31, with the power-map notice at least a week ahead,
so by about 2026-12-22.

## Problem

Observed tenure edges sit on calendar years, but WA terms run from one second Monday in
January to the next (RCW 44.04.021, from 1981). Member-elects (election to term start) aren't
modelled at all.

## Approach

Change the clock first, then the edges, then everything that buckets events by the edges,
then add the new kind. Each step is TDD and green before the next. One PR (B) for #598 and
one (C) for #600. Both deploy together in one release.

## Steps

### PR B — #598

1. **Term clock.** Done, `1efc1ea5`: `legislative_calendar.term_start` / `term_end`, and
   `term_biennium_on` on the statute with the 1981 floor.
2. **`TermCalendar` in the domain.** A protocol in `clearinghouse_domain_legislative.terms`
   (`start(label)`, `end(label)`) with a `CALENDAR_YEAR` default (Jan 1 / Dec 31, today's
   behaviour), and WA's implementation in `legislative_calendar` (`WA_TERMS`).
   - `TenureSpan.valid_from_latest` / `valid_to_earliest` are properties computed from
     `biennium_*_date`. They need the calendar: either store the observed bounds on the span,
     or pass the calendar through the builder and resolve the bounds at build time.
   - Pick the option that keeps `TenureSpan` equality and `source_id` unchanged.
3. **`build_tenure_spans(…, calendar=)`.** Observed `valid_from` = `calendar.start`; the closed
   `valid_to` = `calendar.end`.
   - The pipeline passes `WA_TERMS` everywhere it builds: `conformed.spans`, `house`,
     roster families, and `registry_coverage` through `build_families`.
   - The tenure tests that pin builder output change. Hand-built fixtures stay.
4. **Re-bucket events onto the term calendar.** These move together, each with a test built
   **through the real builder**:
   - `operator_overlay`:
     - `_span_covers`, `_matches_seat`, `_opens_after_seating` (equality at `:183`) and
       `_seating_starts_tenure`;
     - `latest_event_biennium_by_member`, `_in_biennium`, `_synthesize_closed`'s floor and
       `_split_tail`'s key;
     - mover synthesis, and `DEPARTED_ALREADY_CLOSED_GRACE` re-measured from `term_end`.
   - `conformed.movers`: the floors, `_house_ends`, and `_date_committee` (equality at `:284`).
   - `conformed.windows`: `_starts_with`, `_starts_after`, `_ends_with`, and the election
     rule's ceiling. Calendar annotation windows stay calendar.
   - `conformed.capacity`: period floors and the tiling floor.
   - `seat_clipping._in_biennium`.
   - `roster_pdf.projector`: `_biennium` and `ended_before_term`.
5. **dbt gates.**
   - `assignments_boundary_intervals.sql`: a `term_start(label)` / `term_end(label)` macro
     in SQL with the 1981 floor, replacing `make_date(…,1,1)` / `(…,12,31)` in F/C.
   - `seat_overlap_pairs`: re-derive `accepted_seat_overlaps.csv` and review any stale or
     new pair by hand.
6. **Contract.**
   - `ContractRelease` assignments 1.9.0 and role_periods 1.1.0 (semantics only; the hash
     holds).
   - FieldDoc prose: observed = the term's first or last day; `is_active` = open on the term
     clock.
   - Docs: ONTOLOGY (quantization), PIPELINE-CONFORMED(-OCCUPANCY/-ENTITIES), PIPELINE,
     API, RUNBOOK (the term flip on 2027-01-11), MODULES-*.
7. **Dry-run evidence.**
   - A build with today's date and one pinned to 2027-01-11, each diffed against prod's.
   - List the 39 January dated edges with their before and after, plus every changed overlap
     pair.
   - Confirm the row count is flat and every observed edge moves as expected.

### PR C — #600

8. **Evidence staging.**
   - `stg_wsl_first_seen`: walk the raw-store manifests and keep the earliest `fetched_at`
     per (biennium, member) on `sponsors:<next>`.
   - The SOS harvest archives the platform election JSON as `sos-election:<YYYYMMDD>`.
   - `stg_sos_election` carries `isOfficialResults` per wire, and the first `true` is
     certification.
9. **The elect family.**
   - Kinds `chamber-{house,senate}-elect`; roles `seat-elect:…`; role types
     `state_*_elect`.
   - Winners from the seating general: House by (LD, Position); Senate only for LDs on the
     ballot. Never guess.
   - Join a winner to the next-biennium WSL sponsor by `(LD, folded surname, party)`.
   - The span: dated start = election day, `valid_from_latest` = first evidence; dated end
     = `term_end`; `is_active` false.
   - Built from the inputs *before* `without_future_bienniums`.
   - Outside `SINGLE_HOLDER_KINDS` and `SEAT_KINDS`, with a capacity-1 predicate of its own.
   - Operator reasons `called` / `conceded` / `declined`, and no `_synthesize` for elect kinds.
10. **Gates.**
    - The `boundary_intervals` clause for the elect window.
    - `assignments_elect_one_per_seat`, `assignments_elect_winners_covered` (warn) and
      `assignments_elect_not_holdover`.
11. **Contract.**
    - roles 1.4.0; the new staging datasets at 1.0.0; FieldDoc and API.md.
    - Fix the `without_future_bienniums` docstring claim that no assignment cites a
      next-biennium wire.
12. **Release.**
    - The power-map notice with versions, hashes, deltas and the date.
    - Merge B and C, deploy, verify the next nightly, and update the runbook.

## Open questions

None blocking. Q1–Q4 are decided in the spec.
