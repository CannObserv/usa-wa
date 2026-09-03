-- The roster PDF is authoritative from 1889 (statehood). Same floor rule.
select min(year) as earliest
from {{ ref('stg_roster_members') }}
having min(year) > 1889
