-- Every member-elect's seat was on its seating ballot (#600 spec § 2.6): WSL lists all
-- 49 senators for the next biennium, and only the ballot says who was elected, so a
-- Senate member-elect for a holdover's seat would be invented. The House is checked the
-- same way. The seating general is the November before the biennium opens; `seat_winners`
-- keys its seats by the seat role, which a seat-elect role names with its own prefix.
{{ config(severity='error') }}
select a.span_key, a.role_key
from {{ ref('assignments') }} a
where a.span_kind in ('chamber-senate-elect', 'chamber-house-elect')
  and not exists (
      select 1
      from {{ ref('seat_winners') }} w
      where w.election_year = cast(split_part(a.span_start_biennium, '-', 1) as integer) - 1
        and w.role_key = replace(a.role_key, 'seat-elect:', 'seat:')
  )
