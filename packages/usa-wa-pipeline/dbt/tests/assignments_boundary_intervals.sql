-- Each boundary's basis and interval agree (#501 D6; #369 + #360 increment 2).
-- One row per broken invariant, named. `F(b)`/`C(b)` are a biennium's first and
-- last days; the basis says WHERE a date came from, the interval HOW PRECISELY:
--
--   open_end       1. `end_basis = open` ⇔ `valid_to` null ⇔ `valid_to_earliest`
--                     null ⇔ `is_active`
--   open_start     2. a start is never `open` (the enum is `accepted_values`')
--   unordered      3. valid_from ≤ valid_from_latest; valid_to_earliest ≤ valid_to;
--                     valid_from ≤ valid_to_earliest; valid_from_latest ≤ valid_to
--   off_edge       4. `observed` sits on its own biennium edge — the one residual
--                     blind spot is a writer that moves an edge exactly onto one
--   inexact_dated  5. `dated` is exact. (The annotation windows, D6a, widen this
--                     to a calendar month or year in their own change.)
--   interval       6. otherwise the interval is the D6 table's: a start no later
--                     than its biennium's last day, an end no earlier than its
--                     first, each clamped by the other edge — so an inference
--                     (`derived`) never poses as exact
--
-- The reverse of 4 is not asserted: a dated edge may land on a biennium edge
-- (Woodall's 1957-01-01 seating), which is why the basis is carried, not read.
{{ config(severity='error') }}
with a as (
    select
        *,
        make_date(cast(split_part(span_start_biennium, '-', 1) as integer), 1, 1) as start_floor,
        make_date(cast(split_part(span_start_biennium, '-', 1) as integer) + 1, 12, 31)
            as start_ceiling,
        make_date(cast(split_part(span_end_biennium, '-', 1) as integer), 1, 1) as end_floor,
        make_date(cast(split_part(span_end_biennium, '-', 1) as integer) + 1, 12, 31)
            as end_ceiling
    from {{ ref('assignments') }}
)
select span_key, 'open_end' as defect
from a
where (end_basis = 'open') <> (valid_to is null)
   or (valid_to is null) <> (valid_to_earliest is null)
   or (valid_to is null) <> is_active
union all
select span_key, 'open_start' from a where start_basis = 'open'
union all
select span_key, 'unordered'
from a
where valid_from > valid_from_latest
   or valid_to_earliest > valid_to
   or valid_from > valid_to_earliest
   or (valid_to is not null and valid_from_latest > valid_to)
union all
select span_key, 'off_edge'
from a
where (start_basis = 'observed' and valid_from <> start_floor)
   or (end_basis = 'observed' and valid_to <> end_ceiling)
union all
select span_key, 'inexact_dated'
from a
where (start_basis = 'dated' and valid_from_latest <> valid_from)
   or (end_basis = 'dated' and valid_to_earliest <> valid_to)
union all
select span_key, 'interval'
from a
where (
        start_basis <> 'dated'
        and valid_from_latest
            <> greatest(valid_from, least(start_ceiling, coalesce(valid_to, start_ceiling)))
    )
   or (
        end_basis not in ('dated', 'open')
        and valid_to_earliest <> least(valid_to, greatest(end_floor, valid_from))
    )
