-- (resource_id, role_key): one winner per seat per election wire (#412 PR B).
select resource_id, role_key, count(*) as n
from {{ ref('seat_winners') }}
group by 1, 2
having count(*) > 1
