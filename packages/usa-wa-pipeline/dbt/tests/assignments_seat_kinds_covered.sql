-- Every `seat:*` role must be a span_kind the occupancy gate actually covers
-- (#359 CR 122). `assignments_seat_occupancy` is scoped by an allow-list, and an
-- allow-list narrows silently: a new seat-bearing kind — the pre-1965 at-large
-- House family that gate's own comment anticipates, or any future `seat:*` —
-- would simply fall outside it, ungated, with every test still green.
--
-- That is the shape of the bug this whole line of work came from. The daily
-- `succession-invariants` gate scoped to `is_active` and so never saw history,
-- and nothing said so for years. A scope that can shrink without failing is
-- worse than no scope, because it reports the coverage it used to have.
--
-- So: fail loudly the moment a seat role appears that the gate would skip, and
-- force the decision (widen the gate, or exclude the kind deliberately with a
-- reason) instead of letting it be made by omission.
-- `cast(... as varchar)` is not defensive noise: the hermetic build
-- materializes an empty `assignments` whose columns default to INTEGER, so a
-- bare `role_key like 'seat:%'` fails to BIND there rather than returning no
-- rows. Third time this has bitten (#361 tracks the root cause) — until the
-- empty fallback is schema-faithful, any typed operation on this model needs
-- the cast to survive a no-database build.
select cast(span_kind as varchar) as span_kind, count(*) as n
from {{ ref('assignments') }}
where cast(role_key as varchar) like 'seat:%'
  and cast(span_kind as varchar) not in ('chamber-senate', 'chamber-house')
group by 1
