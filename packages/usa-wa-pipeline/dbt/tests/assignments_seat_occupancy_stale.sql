-- Every accepted seat overlap that is no longer CERTAIN, by name (#360 part A,
-- #501 D7).
--
-- The list in `seeds/accepted_seat_overlaps.csv` rots unless an entry that
-- stops matching says so: the #333 `ACCEPTED_OUTAGES` / #452 `ACCEPTED_WARNINGS`
-- rule. An entry goes stale when an operator event or a builder change takes its
-- pair out of the certain tier — resolved outright, or narrowed to a may-overlap,
-- which `assignments_seat_occupancy_may` still lists — or when either span
-- re-keys: a span_key ends in its start biennium and begins with its entity, so
-- a start re-dated across a biennium or a person merge mints a new key. A
-- re-keyed pair that is still certain then also errors in
-- `assignments_seat_occupancy` under its new key: re-adjudicate it there.
--
-- WARN, not error. A stale entry is housekeeping, not a defect in what
-- publishes, so it must not abort the nightly before publish; `build_warnings`
-- fails the stage on any warn, so it still alerts every night until removed.
--
-- Not judged in the hermetic build, which materializes conformed models empty:
-- every entry would read as stale on every pre-commit and CI run, the alert
-- fatigue the old count baseline's mode switch existed to prevent.
{{ config(severity='warn') }}
select x.*
from {{ ref('accepted_seat_overlaps') }} x
{% if env_var('USA_WA_PIPELINE_HERMETIC', '0') == '1' %}
where false
{% else %}
where not exists (
    select 1
    from ({{ seat_overlap_pairs('certain') }}) o
    where o.span_key_lo = x.span_key_lo
      and o.span_key_hi = x.span_key_hi
)
{% endif %}
