-- (biennium, committee_id, member_id, long_name): a chamber mover appears on
-- one committee roster as both "Representative X" and "Senator X" (Doumit,
-- 2001-02) — the honorific is the only discriminator the wire carries.
select biennium, committee_id, member_id, long_name, count(*) as n
from {{ ref('stg_wsl_committee_members') }}
group by 1, 2, 3, 4
having count(*) > 1
