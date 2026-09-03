-- (kind, left_key, right_key, rule): a rule emitting duplicate pairs (or the
-- union double-counting) must fail the build — the registrar consumes this
-- table as its sole input (#302 CR).
select kind, left_key, right_key, rule, count(*) as n
from {{ ref('proposed_links') }}
group by 1, 2, 3, 4
having count(*) > 1
