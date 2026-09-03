-- (biennium, member_id, agency): a mid-biennium chamber move (House→Senate
-- succession) legitimately lists one member under both agencies — 38 such
-- rows in the archive (e.g. Franklin, Morton, Thibaudeau).
select biennium, member_id, agency, count(*) as n
from {{ ref('stg_wsl_sponsors') }}
group by 1, 2, 3
having count(*) > 1
