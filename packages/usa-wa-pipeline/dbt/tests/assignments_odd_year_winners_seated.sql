-- Every odd-year ballot winner's seat is held at the end of that year. Ported
-- from the `house-corroboration` and `senate-corroboration` units, which read
-- canonical and retired (#412 PR B).
--
-- The odd November is the mid-biennium special: it seats a member with no
-- automatic wire signal, so the tenure exists only if an operator recorded the
-- `seated` event (#107). A winner whose seat nobody holds is that missing event
-- — the silent failure the chamber-count gate only notices once the count has
-- already drifted. Victoria Hunt (LD-5 Senate, appointed June 2025, elected
-- that November) is the shape that passes; Teri Hickel (LD-30 House Position 2,
-- won the 2015 special, rostered, never seated) is the shape that fails.
--
-- Keyed on seat EXISTENCE, not occupant identity, exactly as both ported gates
-- were: a seat held by someone other than the ballot winner is more often a
-- ballot-to-roster name change than a missing succession. Those units reported
-- such seats as `mismatched` and never gated them; that report is not ported.
--
-- WIDER than the gates it replaces, and probed differently:
--   * every archived odd year, not only the current biennium's. Hickel was
--     found by a manual audit, because the daily gate never looked back and
--     its history sweep was report-only;
--   * probed at December 31 of the election year, not today. The daily gate
--     checked the open cohort, so a 2025 winner who resigned in 2026 would
--     have paged as "missing" while the seat was simply vacant.
-- The ported sweep's caveat carries over: a special certified into the
-- following January would fail here. None has, 2009-2025; if one does, name it
-- rather than move the probe date.
--
-- `seat_winners` carries the SOS normalizers' winner per race, keyed by the
-- role_key assignments carry, so this join re-parses no race label.
--
-- Senate corroboration also field-cited `valid_from` on the SOS wire. That
-- writer is dropped (#412 Q3): the published citations exclude SOS by design.
--
-- A plain `error` with no baseline: 0 on the production build 2026-09-27.
{{ config(severity='error') }}
select
    w.election_year,
    w.role_key,
    w.resource_id
from {{ ref('seat_winners') }} w
where w.election_year % 2 = 1
  and not exists (
      select 1
      from {{ ref('assignments') }} a
      where a.role_key = w.role_key
        and a.valid_from <= make_date(w.election_year, 12, 31)
        and (a.valid_to is null or a.valid_to >= make_date(w.election_year, 12, 31))
  )
