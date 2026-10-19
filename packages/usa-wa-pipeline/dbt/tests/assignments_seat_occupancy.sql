-- A seat holds no more principals than its capacity (#359, #501 D7): every pair
-- the CERTAIN tier of `seat_overlap_pairs()` finds is an error unless the named
-- list accepts that exact pair (#360 part A). The predicate, its tiers and why
-- each clause is spelled the way it is live in `macros/seat_overlap_pairs.sql`.
--
-- THE CERTAIN TIER, not the may tier. A pair that overlaps only within the width
-- of an imprecise boundary — a one-biennium undated succession, a month-dated
-- appointment — may be a clean handoff, so it is listed rather than failed
-- (`assignments_seat_occupancy_may`). A pair whose INNER bounds overlap overlaps
-- whatever the true dates are: a defect, or an adjudicated residue.
--
-- THE LIST, `seeds/accepted_seat_overlaps.csv`, holds certain-tier pairs only
-- (owner, #360, 2026-10-17: a pair that leaves the certain tier leaves the list).
-- Each entry is one unordered span_key pair, stored `lo < hi` (the macro
-- normalises the same way), with a category from a closed vocabulary (the seed's
-- `accepted_values`) and the roster evidence for it. Adjudicated against the
-- roster PDF on #360, 2026-10-17: every residue the clip declines and that no
-- operator event dates. Two categories retired with their last rows in #501:
-- `substitute` (now `membership_type`) and `basis_inferred` (now an explicit
-- boundary basis). The categories:
--
--   election_only    the successor is dated only by an election, which is not
--                    a seating (#226: the ballot date would open the span early)
--   coarse_date      the only stated boundary is month or year precision
--   merged_tenure    one span is two tenures merged around the other (#267)
--   undated          the successor's start is undated in the roster
--   never_served     a span for someone the roster says never served
--
-- An entry stops matching when its pair leaves the certain tier, or when either
-- span re-keys (a start re-dated across a biennium, or a person merge re-pointing
-- the entity). `assignments_seat_occupancy_stale` reports it by name; remove it.
--
-- Recording an operator event that resolves a listed pair: remove the entry in
-- the same change, or the stale check warns. Adding a pair: only with its
-- evidence, on its issue — the list is the adjudication, not a mute button.
{{ config(severity='error') }}
select o.*
from ({{ seat_overlap_pairs('certain') }}) o
where not exists (
    select 1
    from {{ ref('accepted_seat_overlaps') }} x
    where x.span_key_lo = o.span_key_lo
      and x.span_key_hi = o.span_key_hi
)
