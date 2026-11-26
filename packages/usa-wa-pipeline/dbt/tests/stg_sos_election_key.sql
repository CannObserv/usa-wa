-- (election_date, sha256): one row per distinct election JSON, at its first fetch (#600).
select election_date, sha256, count(*) as n
from {{ ref('stg_sos_election') }}
group by 1, 2
having count(*) > 1
