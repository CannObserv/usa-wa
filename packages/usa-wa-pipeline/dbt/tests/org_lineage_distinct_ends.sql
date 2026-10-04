-- A published link joins two entities (#447). The registry table's CHECK bars a
-- self-link on WSL ids; a merge tombstone can still resolve both ends of
-- `A -> B` to one survivor, which no consumer can apply. Merge or link — the
-- two judgments contradict each other, so one is wrong.
{{ config(severity='error') }}
select *
from {{ ref('org_lineage') }}
where subject_entity_id = linked_entity_id
