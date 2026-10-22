-- `seat_overlap_pairs(tier)` — the occupancy predicate, held once (#360 part A,
-- #501 D7). Three tests call it: the gate (`assignments_seat_occupancy`, the
-- certain tier), its stale check (`assignments_seat_occupancy_stale`) and the
-- may-tier listing (`assignments_seat_occupancy_may`), so none carries a copy
-- that could drift from the others.
--
-- THE PREDICATE (#501 D7, shared with power-map). For a role R and an instant t
-- inside a period of R whose `max_holders` m is not null:
--
--     |{ principals a on R : a.lo <= t < coalesce(a.hi, infinity) }|  <=  m
--
-- The count is of distinct ENTITIES (one person holding a seat twice, across a
-- dormancy gap, is two tenures and one holder), of PRINCIPALS only — a
-- substitute serving under a principal on leave is not a second holder
-- (`membership_type`, #501 D5) — over chamber seats only: party membership and
-- committee seats are legitimately multi-holder, which `role_periods` states
-- with a null `max_holders`, so the constraint is stated by KIND rather than
-- by an exception list that would rot.
--
-- Intervals are HALF-OPEN. A span ending the day its successor begins is how a
-- mid-term succession is spelled, and counting it would bury the real conflicts
-- under hundreds of clean transitions. An open span (`hi is null`) is still
-- serving, so it covers every later instant: spelled as an explicit null branch,
-- because `t < null` is null and SQL drops it — the plain form once made the gate
-- blind to every conflict involving a sitting member (`test_an_open_span_still_conflicts`).
--
-- The count peaks at some principal's start, or where capacity drops — at a
-- period's start (#501 correction 7) — so those are the only instants checked.
-- A pair is reported when both cover an instant whose count exceeds m.
--
-- TWO TIERS (#501 D7), from the boundary intervals (D6):
--
--   may      [valid_from, valid_to)                    — the outer bounds, today's
--            predicate, and the one power-map applies: every pair that MAY overlap
--   certain  [valid_from_latest, valid_to_earliest)    — the inner bounds: a pair
--            that overlaps whatever the true boundaries are. Empty where latest
--            >= earliest, so a one-biennium undated succession is never certain.
--
-- CAPACITY comes from `role_periods` (#501 D1), the same periods the counterpart
-- clip reads, which retired the roster exclusion this macro used to carry (the
-- 1889 multi-member Senate: LD-19 seated five). A seat with no covering period —
-- a tiling defect `role_periods_tiled` reports — is read as holding ONE: absent
-- proof of a multi-member seat, the conservative reading polices.
{% macro seat_overlap_pairs(tier='may') %}
    {%- if tier == 'certain' -%}
        {%- set lo, hi = 'valid_from_latest', 'valid_to_earliest' -%}
    {%- elif tier == 'may' -%}
        {%- set lo, hi = 'valid_from', 'valid_to' -%}
    {%- else -%}
        {{ exceptions.raise_compiler_error("seat_overlap_pairs: unknown tier " ~ tier) }}
    {%- endif -%}
    with principals as (
        select
            role_key,
            span_key,
            entity_id,
            valid_from,
            valid_to,
            {{ lo }} as lo,
            {{ hi }} as hi
        from {{ ref('assignments') }}
        where span_kind in ('chamber-senate', 'chamber-house')
          and membership_type = 'principal'
          and ({{ hi }} is null or {{ lo }} < {{ hi }})
    ),
    instants as (
        select role_key, lo as t from principals
        union
        select p.role_key, rp.valid_from as t
        from {{ ref('role_periods') }} rp
        join (select distinct role_key from principals) p on p.role_key = rp.role_key
    ),
    holding as (
        select i.role_key, i.t, p.span_key, p.entity_id, p.valid_from, p.valid_to
        from instants i
        join principals p
            on p.role_key = i.role_key
            and p.lo <= i.t
            and (p.hi is null or i.t < p.hi)
    ),
    excess as (
        select h.role_key, h.t
        from holding h
        left join {{ ref('role_periods') }} rp
            on rp.role_key = h.role_key
            and rp.valid_from <= h.t
            and (rp.valid_to is null or h.t <= rp.valid_to)
        group by h.role_key, h.t, rp.role_key, rp.max_holders
        having (rp.role_key is null and count(distinct h.entity_id) > 1)
            or (rp.max_holders is not null and count(distinct h.entity_id) > rp.max_holders)
    )
    select distinct
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
    from excess e
    join holding a on a.role_key = e.role_key and a.t = e.t
    join holding b on b.role_key = e.role_key and b.t = e.t and a.entity_id < b.entity_id
{% endmacro %}
