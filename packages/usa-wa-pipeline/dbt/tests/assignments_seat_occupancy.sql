-- A seat holds ONE person at a time (#359): every overlap `seat_overlap_pairs()`
-- finds is an error unless the named list accepts that exact pair (#360 part A).
-- The predicate, and why each clause of it is spelled the way it is, lives in
-- `macros/seat_overlap_pairs.sql`.
--
-- THE LIST, `seeds/accepted_seat_overlaps.csv`, replaced a count baseline of
-- 35. A count could mask one new conflict behind one repaired elsewhere; a name
-- cannot. Each entry is one unordered span_key pair, stored `lo < hi` (the
-- macro normalises the same way), with a category from a closed vocabulary
-- (the seed's `accepted_values`) and the roster evidence for it. Adjudicated
-- against the roster PDF on #360, 2026-10-17: every residue the clip declines
-- (`seat_clipping`'s `unclipped` reasons) and that no operator event can date
-- without inventing a boundary:
--
--   substitute       a military substitute under a principal on leave — a true
--                    overlap; #501 publishes it as `membership_type=substitute`
--   basis_inferred   a real dated event the clip reads as quantized because it
--                    lies on a biennium edge; #501 makes the basis explicit
--   election_only    the successor is dated only by an election, which is not
--                    a seating (#226: the ballot date would open the span early)
--   coarse_date      the only stated boundary is month or year precision
--                    (#501's annotation windows, D6a)
--   merged_tenure    one span is two tenures merged around the other (#267)
--   undated          the successor's start is undated in the roster
--   never_served     a span for someone the roster says never served
--
-- The 1889 multi-member Senate is not on the list: the macro's roster exclusion
-- removes it, and #501's `role_periods` retires that exclusion.
--
-- An entry stops matching when its pair stops overlapping, or when either span
-- re-keys (a start re-dated across a biennium, or a person merge re-pointing the
-- entity). `assignments_seat_occupancy_stale` reports it by name; remove it.
--
-- Recording an operator event that resolves a listed pair: remove the entry in
-- the same change, or the stale check warns. Adding a pair: only with its
-- evidence, on its issue — the list is the adjudication, not a mute button.
{{ config(severity='error') }}
select o.*
from ({{ seat_overlap_pairs() }}) o
where not exists (
    select 1
    from {{ ref('accepted_seat_overlaps') }} x
    where x.span_key_lo = o.span_key_lo
      and x.span_key_hi = o.span_key_hi
)
