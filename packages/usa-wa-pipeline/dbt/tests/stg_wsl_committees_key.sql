-- (biennium, committee_id) is the staging committee key
select biennium, committee_id, count(*) as n
from {{ ref('stg_wsl_committees') }}
group by 1, 2
having count(*) > 1
