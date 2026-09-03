-- (election_date, race_name, race_jurisdiction_name, ballot_name): one filing
-- per candidate per race per election. Vacuous while the store is empty (#307)
-- — the contract lands with the model, not after the first harvest (#302 CR).
select election_date, race_name, race_jurisdiction_name, ballot_name, count(*) as n
from {{ ref('stg_sos_filings') }}
group by 1, 2, 3, 4
having count(*) > 1
