-- Fewer open seats than the chamber has: a VACANCY, reported at `warn`. The
-- other half of `assignments_chamber_excess` (#412 PR B) — see there for why
-- the two sides of the ported equality gate are split.
--
-- A vacancy is a real state: a death, a resignation awaiting its appointment,
-- or the 2027-01-01 rollover before the new biennium's wire lands. Blocking the
-- build on one would stop publishing for as long as the seat stays empty, and
-- the published tables would be no more right for it. A vacancy that never
-- fills is a missing `seated`, and the warning is where it shows.
--
-- Vacuous when a chamber has no spans at all, never when all are closed: the
-- hermetic build materializes conformed models empty (#361), and a warning on
-- every pre-commit would teach everyone to scroll past the real one — the
-- lesson `assignments_seat_occupancy`'s mode-aware baseline records.
--
-- Literals pinned to `usa_wa_common.seats` by `test_chamber_counts`.
{{ config(severity='warn') }}
with seats (span_kind, seats) as (
    values ('chamber-senate', 49), ('chamber-house', 98)
),

chambers as (
    select
        span_kind,
        count(*) filter (where is_active) as open_occupancies
    from {{ ref('assignments') }}
    group by span_kind
)

select
    s.span_kind,
    s.seats,
    c.open_occupancies
from seats s
join chambers c on c.span_kind = s.span_kind
where c.open_occupancies < s.seats
