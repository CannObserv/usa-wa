-- (entity_id, span_kind, span_discriminator, span_start_biennium): the span's
-- identity — the same 4-part key the canonical assignment source_id encodes,
-- with the member id replaced by the registry entity (#309). A duplicate here
-- means two spans claim one tenure start, which the engine cannot produce
-- unless two member ids resolved to one entity (a merge the join must fold).
select entity_id, span_kind, span_discriminator, span_start_biennium, count(*) as n
from {{ ref('assignments') }}
group by 1, 2, 3, 4
having count(*) > 1
