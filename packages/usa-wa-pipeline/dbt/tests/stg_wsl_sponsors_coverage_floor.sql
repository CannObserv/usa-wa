-- WSL serves sponsor rosters from 1991-92 (the adapter's CoverageClaim floor).
-- A populated store whose earliest biennium is later than the floor lost
-- history. Vacuous on an empty store (the hermetic gate).
select min(cast(biennium as varchar)) as earliest
from {{ ref('stg_wsl_sponsors') }}
having min(cast(biennium as varchar)) > '1991-92'
