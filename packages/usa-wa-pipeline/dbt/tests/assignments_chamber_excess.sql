-- No chamber has more open seats than it has seats. Ported from the
-- `succession-invariants` unit's chamber-count check, which read canonical and
-- retired (#412 PR B).
--
-- An operator succession event is durable once entered, but a MISSING one is
-- silent: a member dies, nobody records it, and a ghost-open span inflates the
-- chamber — the record is wrong for up to a biennium. 50 open senators or 99
-- open representatives is that ghost.
--
-- ONE-SIDED, unlike the gate it replaces. That gate asserted strict equality,
-- and a failed dbt test aborts the nightly before registrar and publish — so a
-- straight port would block publishing on every legitimate vacancy and on the
-- 2027-01-01 rollover. A vacancy is a real state; an excess is a defect. The
-- low side is `assignments_chamber_vacancy`, at `warn`.
--
-- Counts open OCCUPANCIES, not distinct seats, as the ported gate did: a
-- doubly-occupied seat counts twice, and that inflation is itself the signal.
--
-- The literals are WA's chamber sizes, `usa_wa_common.seats.SENATE_SEATS` /
-- `HOUSE_SEATS`; SQL cannot import them, so `test_chamber_counts` pins these
-- to those. Hermetic-safe: an empty table has no chamber to exceed.
{{ config(severity='error') }}
with seats (span_kind, seats) as (
    values ('chamber-senate', 49), ('chamber-house', 98)
)
select
    s.span_kind,
    s.seats,
    count(*) as open_occupancies
from seats s
join {{ ref('assignments') }} a
    on a.span_kind = s.span_kind
    and a.is_active
group by s.span_kind, s.seats
having count(*) > s.seats
