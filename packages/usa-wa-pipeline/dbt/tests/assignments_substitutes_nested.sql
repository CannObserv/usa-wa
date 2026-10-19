-- A substitute serves INSIDE a principal's tenure on the same seat, on dates the
-- roster states (#501 D5): the guards on `SUBSTITUTE_TENURES`, over the built
-- corpus. An entry that names no built span at all is refused earlier, by the
-- `assignments` model itself (`unmatched_memberships`).
{{ config(severity='error') }}
select s.span_key, s.role_key, s.valid_from, s.valid_to, s.start_basis, s.end_basis
from {{ ref('assignments') }} s
where s.membership_type = 'substitute'
  and (
      s.start_basis <> 'dated'
      or s.end_basis <> 'dated'
      or not exists (
          select 1
          from {{ ref('assignments') }} p
          where p.role_key = s.role_key
            and p.membership_type = 'principal'
            and p.entity_id <> s.entity_id
            and p.valid_from <= s.valid_from
            and (p.valid_to is null or s.valid_to <= p.valid_to)
      )
  )
