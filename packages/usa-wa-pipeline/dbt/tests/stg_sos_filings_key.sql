-- (election_date, race_name, race_jurisdiction_name, ballot_name): one filing
-- per candidate per race per election. Vacuous while the store is empty (#307).
-- severity warn (#302 CR 49): this key is a contract STATED before any real
-- WhoFiled wire has landed (the first nightly's fetches erred) — an unverified
-- guess must not abort the whole chain (dbt failure kills registrar + publish).
-- Ratchet to error once a real harvest verifies the key.
{{ config(severity='warn') }}
select election_date, race_name, race_jurisdiction_name, ballot_name, count(*) as n
from {{ ref('stg_sos_filings') }}
group by 1, 2, 3, 4
having count(*) > 1
