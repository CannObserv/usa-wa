-- A succeeded or merged predecessor is inactive: #124's INV2, ported from the
-- `committee-lineage-invariants` unit (retired, and blind to Joint/Other links
-- since it read canonical `org_type = 'committee'` only). #447.
--
-- `organizations` DERIVES this (`lineage.retired_entities` feeds `org_rows`), so
-- the test guards the wiring rather than the operator: both models read the
-- registry, `org_lineage` directly and `organizations` through it, and a
-- refactor that drops the hand-off would put Civic Health 35341 back to
-- `active=true` with every other test green.
--
-- `split_from` is exempt: a split child stays live beside its parent (#124
-- OQ3). Production carried one violation before the derivation (35341), so a
-- plain `error`. One row per offending org, not per link (dbt counts rows).
{{ config(severity='error') }}
select
    o.entity_id,
    o.org_type,
    count(*) as retiring_links
from {{ ref('organizations') }} o
join {{ ref('org_lineage') }} l
    on l.subject_entity_id = o.entity_id
where l.slug in ('succeeded_by', 'merged_with')
  and o.active
group by o.entity_id, o.org_type
