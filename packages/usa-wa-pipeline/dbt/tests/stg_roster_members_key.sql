-- (year, chamber, district, "order", name): order is SEAT-LINEAGE order
-- (#229) — a mid-year successor inherits the seat's order, so two holders of
-- one seat share (year, chamber, district, order) and the name completes the
-- key (58 succession pairs in the roster).
select year, chamber, district, "order", name, count(*) as n
from {{ ref('stg_roster_members') }}
group by 1, 2, 3, 4, 5
having count(*) > 1
