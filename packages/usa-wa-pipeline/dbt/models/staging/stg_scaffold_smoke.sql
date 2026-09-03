-- Scaffold smoke model (#303): proves seed → model → schema/data test end to
-- end so the commit gate and the suite exercise the whole harness rather than
-- an empty project. Retired when the first real staging models land (#306).
select id, label
from {{ ref('scaffold_smoke') }}
