-- At most one member-elect per seat-elect role per biennium (#600 spec § 2.6): the
-- builder never guesses between two listed members on one won seat, so a second row
-- here is a builder defect, not a close race.
{{ config(severity='error') }}
select role_key, span_start_biennium, count(*) as n
from {{ ref('assignments') }}
where span_kind in ('chamber-senate-elect', 'chamber-house-elect')
group by 1, 2
having count(*) > 1
