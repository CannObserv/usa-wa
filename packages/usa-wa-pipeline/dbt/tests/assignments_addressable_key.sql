-- (member_id, span_kind, span_discriminator, span_start_biennium): the span's
-- ADDRESSABLE key — what `/api/v1/assignments/{assignment_id}` takes and what
-- the citations artifact cites an assignment by (#313). Distinct from
-- `assignments_key`, which keys on the registry entity: this one keys on the
-- member id, and the two differ exactly when a merge folds two member ids onto
-- one entity.
--
-- `source` is deliberately NOT in the group: the whole point is that the 4-part
-- key is unique WITHOUT it, because the two families key in disjoint identity
-- spaces (numeric WSL ids, `<fold>:<year>` roster ones). If that ever stops
-- holding, the API cannot address a span at all — it answers 500 rather than
-- flipping a coin — so the assumption is pinned here rather than assumed there.
select member_id, span_kind, span_discriminator, span_start_biennium, count(*) as n
from {{ ref('assignments') }}
group by 1, 2, 3, 4
having count(*) > 1
