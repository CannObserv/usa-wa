-- One member holds one legislative seat at a time. Ported from the
-- `succession-invariants` unit's member-side duplicate check, which reads
-- canonical and retires (#412 PR B).
--
-- The seat-side twin is `assignments_seat_occupancy` (two holders, one seat).
-- This is the other half: ONE entity with two overlapping seat spans, whether
-- on two seats (a missing `departed` left the old seat open behind a move), on
-- the same seat twice (a merged tenure, #267), or in BOTH chambers at once.
--
-- WIDER than the gate it replaces, on purpose. That gate probed only the open
-- cohort, so a conflict that had since closed was invisible to it forever
-- (#119), and the history audit that could see one was report-only. The
-- conformed tier is rebuilt whole every night, so history is as cheap to gate
-- as today: 0 conflicts across all of history on the production build
-- 2026-09-27, hence a plain `error` with no baseline.
--
-- A shared boundary is a handoff, not an overlap (a redistricting renumber, or
-- the successor tenure of a chamber-internal move), hence the strict `<`. An
-- open span (`valid_to is null`) overlaps anything that starts after it; the
-- explicit null branches say so without a sentinel date (see
-- `assignments_seat_occupancy` for why).
--
-- ACROSS chambers, unlike the ported gate, which paired spans within one
-- chamber only (CR 5). House and Senate at once is the chamber-mover shape
-- (#145): a representative appointed to the Senate whose House `vacated` was
-- never recorded. Once the House seat has a successor the chamber count is back
-- to 98, so no other gate sees it. 0 across all history on the production
-- build 2026-09-27. A same-day move is a handoff, as above.
{{ config(severity='error') }}
select
    a.entity_id,
    a.role_key as role_a,
    a.valid_from as from_a,
    a.valid_to as to_a,
    b.role_key as role_b,
    b.valid_from as from_b,
    b.valid_to as to_b
from {{ ref('assignments') }} a
join {{ ref('assignments') }} b
    on a.entity_id = b.entity_id
    and a.span_key < b.span_key
where a.span_kind in ('chamber-senate', 'chamber-house')
  and b.span_kind in ('chamber-senate', 'chamber-house')
  and (b.valid_to is null or a.valid_from < b.valid_to)
  and (a.valid_to is null or b.valid_from < a.valid_to)
