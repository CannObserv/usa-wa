-- `role_periods` tiles every held role (#501 D1): from the floor of its earliest
-- tenure to one open last period, with no gaps, no overlaps and a capacity of at
-- least one where there is a limit. One row per defect, named.
--
-- A period's `valid_to` is its last day, INCLUSIVE, so the next period opens the
-- day after: `next.valid_from = prev.valid_to + 1`. (An assignment's `valid_to` is
-- a handoff date instead; the datapackage describes both.)
--
-- `uncovered_tenure` is the reason the floor is the earliest `valid_from`'s
-- biennium rather than the earliest key's: a start can sit before its key
-- (the #282 lookback, the clip), and the occupancy gate reads a seat with no
-- covering period as holding one (#501 correction 8).
{{ config(severity='error') }}
with p as (
    select
        role_key,
        valid_from,
        valid_to,
        max_holders,
        lead(valid_from) over (partition by role_key order by valid_from) as next_from
    from {{ ref('role_periods') }}
),
first_period as (
    select role_key, min(valid_from) as first_from
    from {{ ref('role_periods') }}
    group by role_key
)
select role_key, valid_from, 'gap_or_overlap' as defect
from p
where next_from is not null and (valid_to is null or next_from <> valid_to + 1)
union all
select role_key, valid_from, 'closed_last_period'
from p
where next_from is null and valid_to is not null
union all
select role_key, min(valid_from), 'open_periods_not_one'
from p
group by role_key
having count(*) filter (where valid_to is null) <> 1
union all
select role_key, valid_from, 'max_holders_below_one'
from p
where max_holders < 1
union all
select a.role_key, min(a.valid_from), 'uncovered_tenure'
from {{ ref('assignments') }} a
left join first_period f on f.role_key = a.role_key
where f.first_from is null or a.valid_from < f.first_from
group by a.role_key
