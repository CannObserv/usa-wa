-- (election_date, race, candidate): one candidate per race per election
select election_date, race, candidate, count(*) as n
from {{ ref('stg_sos_results') }}
group by 1, 2, 3
having count(*) > 1
