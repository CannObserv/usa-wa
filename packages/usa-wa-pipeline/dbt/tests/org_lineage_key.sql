-- One published row per (subject, slug, linked) edge (#447). The edge is the
-- identity a consumer applies retraction-as-absence in: a year-only correction
-- supersedes the old row and refines the edge in place (#127), so two CURRENT
-- rows on one edge are two years asserted at once — supersede one.
--
-- The same pair under two slugs is two edges, and legitimate: production's
-- 8265 split_from 438 + 8265 succeeded_by 438 (a dormancy blip on a live head).
{{ config(severity='error') }}
select
    subject_entity_id,
    slug,
    linked_entity_id,
    count(*) as attestations
from {{ ref('org_lineage') }}
group by subject_entity_id, slug, linked_entity_id
having count(*) > 1
