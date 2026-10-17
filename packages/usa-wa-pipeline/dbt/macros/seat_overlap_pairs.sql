-- `seat_overlap_pairs()` — the occupancy predicate, held once (#360 part A).
-- The gate (`assignments_seat_occupancy`) and its stale check
-- (`assignments_seat_occupancy_stale`) both call it, so neither carries a copy
-- that could drift from the other; #501's certain tier is its next caller.
--
-- A seat holds ONE person at a time (#359). Two distinct entities whose tenures
-- overlap on the same seat is either bad upstream data or a span the builder
-- failed to clip — both publishable today, because nothing else checks it.
--
-- Nothing else does, despite appearances: `assignments_key` tests span IDENTITY
-- (one tenure start per entity), which two different holders of one seat pass
-- cleanly, and the daily `succession-invariants` gate scopes to `is_active`
-- rows — the CURRENT cohort only. History has never been gated, and the tier
-- that gate reads retires in #314.
--
-- Scoped to `seat:*` roles via span_kind. Party membership and committee seats
-- are legitimately multi-holder, so the constraint is stated by KIND rather
-- than by an exception list that would rot. One caveat if House coverage ever
-- deepens past 1965: pre-1965 House seats were fungible at-large, two per
-- district with no Position (usa-wa#101), so a position-less `seat:house:ld-N`
-- would legitimately carry two concurrent holders and must be excluded here.
-- Every `chamber-house` span today carries `position-N`, so none exist yet.
--
-- A shared boundary date is a handoff, not an overlap: a span ending the day
-- its successor begins is how a mid-term succession is spelled, and counting it
-- would bury the real conflicts under hundreds of clean transitions. Spelled
-- `is distinct from` rather than `<>`: an open span's `valid_to` is NULL, and
-- `NOT (false OR NULL)` is NULL, which SQL drops — so the plain form made the
-- gate BLIND to every conflict involving a currently-serving member, the live
-- ones that matter most. Caught by `test_an_open_span_still_conflicts`.
--
-- An open span (`valid_to is null`) means "still serving", so it overlaps
-- anything that starts after it. Spelled as an explicit null branch rather than
-- `coalesce(valid_to, date '9999-12-31')`, because the sentinel is a smell: a
-- real date standing in for "no date" is one comparison away from being read
-- as one.
--
-- MULTI-MEMBER DISTRICTS are excluded, because they are not conflicts (#360).
-- Washington's 1889 legislature seated multi-member senate districts — 35
-- senators across 24 districts, LD-19 alone carrying five — so `seat:senate:ld-N`
-- collapses genuinely distinct seats into one role_key and this gate would read
-- five lawful senators as a five-way fight.
--
-- The roster draws the distinction itself, which is what makes capacity
-- checkable rather than guessed: of 156 district-years with more than one senate
-- row, 151 carry succession annotations ("Resigned January 13, 1997", "Deceased
-- Aug. 9, 1971") and 5 are bare — all 1889. An annotated extra row is a
-- SUCCESSOR within one seat; a bare extra row is a SEAT.
--
-- The reading is deliberately conservative: ANY annotation in a district-year
-- means capacity 1. Treating a partly-annotated year as multi-member would
-- license exactly the conflict this gate exists to catch, so ambiguity resolves
-- toward policing rather than excusing.
--
-- NOT keyed on the roster's `order` column, which looks like a seat index and is
-- not: it is alphabetical by surname within a district-year, and 101 of 417
-- people change order between bienniums. Keying on it would mint seat identities
-- that never existed, make ordinary succession look like seat-hopping, and move
-- historical `role_key` values — the key a subscriber matches a seat on.
{% macro seat_overlap_pairs() %}
    select
        a.role_key,
        -- the pair, unordered: the order `accepted_seat_overlaps` stores it in
        least(a.span_key, b.span_key) as span_key_lo,
        greatest(a.span_key, b.span_key) as span_key_hi,
        a.entity_id as entity_a,
        a.valid_from as from_a,
        a.valid_to as to_a,
        b.entity_id as entity_b,
        b.valid_from as from_b,
        b.valid_to as to_b
    from {{ ref('assignments') }} a
    join {{ ref('assignments') }} b
        on a.role_key = b.role_key
        and a.entity_id < b.entity_id
    where a.span_kind in ('chamber-senate', 'chamber-house')
      and b.span_kind in ('chamber-senate', 'chamber-house')
      and (b.valid_to is null or a.valid_from <= b.valid_to)
      and (a.valid_to is null or b.valid_from <= a.valid_to)
      and a.valid_to is distinct from b.valid_from
      and b.valid_to is distinct from a.valid_from
      and not exists (
          select 1
          from {{ ref('stg_roster_members') }} r
          where r.chamber = regexp_extract(a.role_key, 'seat:(\w+):', 1)
            and cast(r.district as varchar) = regexp_extract(a.role_key, 'ld-(\d+)', 1)
          group by r.chamber, r.district, r.year
          -- `year` is a grouping key, so min(r.year) below is identity on it — the
          -- aggregate is only there to be legal in HAVING (CR 128)
          having count(*) > 1
             and count(r.annotation) = 0
             -- the multi-member biennium must cover the overlap, not merely exist
             and make_date(min(r.year) + 1, 12, 31) >= greatest(a.valid_from, b.valid_from)
             and (
                 (a.valid_to is null and b.valid_to is null)
                 or make_date(min(r.year), 1, 1) <= least(
                     coalesce(a.valid_to, b.valid_to),
                     coalesce(b.valid_to, a.valid_to)
                 )
             )
      )
{% endmacro %}
