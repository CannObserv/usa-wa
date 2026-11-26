-- (biennium, member_id): one first listing per member and biennium (#600).
select biennium, member_id, count(*) as n
from {{ ref('stg_wsl_first_seen') }}
group by 1, 2
having count(*) > 1
