-- `role_periods` is keyed (role_key, valid_from) (#501 D1): one period per role
-- per start. A duplicate would let two capacities claim one instant.
{{ config(severity='error') }}
select role_key, valid_from, count(*) as periods
from {{ ref('role_periods') }}
group by role_key, valid_from
having count(*) > 1
