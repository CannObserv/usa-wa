-- Every cycle among forward-flow links reads in time order (#447). A literal
-- "no cycles" would refuse correct history: WSL re-uses committee ids, so a
-- round-trip rename is a real cycle — five in production, 924 -> 966 -> 924
-- (1993/1995) among them (#126). `org_lineage_cycles` emits only the cycles no
-- history can produce: years that wrap more than once going around (a same-year
-- reversal — one link has its direction wrong), or an undated link on a cycle.
-- The logic and its tests: `usa_wa_pipeline.conformed.lineage.untimely_cycles`.
{{ config(severity='error') }}
select *
from {{ ref('org_lineage_cycles') }}
