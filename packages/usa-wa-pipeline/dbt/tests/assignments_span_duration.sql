-- A published tenure has duration (#363). `valid_to` is either open, or strictly
-- after `valid_from`; a span covering no time at all is never a fact about the
-- world, and an inverted one is a boundary applied backwards.
--
-- This is the check whose absence let #363 publish silently. The occupancy gate
-- beside it needs TWO distinct holders overlapping on one seat, so a single span
-- collapsed to a point is invisible to it; `assignments_key` tests span identity,
-- which one degenerate span passes cleanly. Derek Stanford's LD-1 Senate tenure
-- was closed by a person-scoped `departed` at the instant a `seated` opened it,
-- and 18 months of a sitting senator's service left the published record with
-- nothing anywhere to report it.
--
-- A plain `error` with no baseline, unlike the occupancy gate: the corpus is
-- already clean on this (0 zero-length, 0 inverted once #363 landed), so there
-- is nothing to ratchet and a threshold would only be somewhere for a
-- regression to hide.
--
-- Short is not the same as empty. Washington seats military substitutes for
-- days at a time — Jon Wyss held LD-6 for two days in 2005 while Brad Benson was
-- on military leave — so the gate asserts DURATION, never a minimum (#362).
--
-- The cast keeps the hermetic build honest: it materializes conformed models
-- EMPTY, which types the columns INTEGER, and a bare date comparison fails to
-- BIND there rather than returning no rows (#361).
{{ config(severity='error') }}
select
    entity_id,
    role_key,
    valid_from,
    valid_to
from {{ ref('assignments') }}
where valid_to is not null
  and cast(valid_to as date) <= cast(valid_from as date)
