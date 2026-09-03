-- Every published assignment names a role the dimension defines (#309 inc 4).
-- The whole point of a deterministic structural key is that the join needs no
-- identity mediation — so a dangling role_key means the derivation forked.
select a.role_key, count(*) as n
from {{ ref('assignments') }} a
left join {{ ref('roles') }} r on r.role_key = a.role_key
where r.role_key is null
group by 1
