-- An inactive organization has no live member: #124's INV1, ported from the
-- `committee-lineage-invariants` unit, which reads canonical and retires
-- (#428, ahead of #412 PR E). A dissolved committee must not read as having
-- current members.
--
-- Not a tautology. `active` comes from the committees-roster wire (a meeting
-- window for Joint/Other bodies); a live assignment comes from the membership
-- wire. Both are read on ONE clock (`spans.current_biennium`), so a rollover
-- closes the old spans and retires the old committees in the same build, and
-- the calendar alone cannot fire this.
--
-- WIDER than the unit it replaces, which checked org_type = 'committee' only:
-- a live member of a party declared inactive is the same defect. 0 on the
-- production build 2026-09-28 across every org type, hence a plain `error`.
-- One row per offending org, not per member (dbt counts rows).
{{ config(severity='error') }}
select
    o.entity_id,
    o.org_type,
    count(*) as live_members
from {{ ref('organizations') }} o
join {{ ref('roles') }} r
    on r.org_entity_id = o.entity_id
join {{ ref('assignments') }} a
    on a.role_key = r.role_key
where not o.active
  and a.is_active
group by o.entity_id, o.org_type
