-- A tenure starts inside the biennium its key names (#272), ported from the
-- `succession-invariants` unit, which reads canonical and retires (#412 PR B).
--
-- A span's key ends in its TENURE-START biennium, so `valid_from` belongs in
-- it. A span keyed 2003-04 that begins in 2016 records a thirteen-year tenure
-- as six weeks, and no other gate can see it: the occupancy gates look for
-- DUPLICATE holders, and one holder with wrong dates is not a duplicate. Two
-- live rows had exactly this shape before #274 — Hans Dunshee's LD-44 House
-- tenure (`seated 2016-02-29` resolved onto a tenure that began in 2003) and
-- Mike Kreidler's LD-22 Senate tenure — and both were silent for months.
--
-- One-sided, as the Postgres predicate was. A start INSIDE the key biennium is
-- the ordinary mid-biennium appointee. A start BEFORE it is a derived edge:
-- counterpart clipping (#360) moves a quantized `valid_from` earlier onto a
-- predecessor's dated exit, and that is a correction, not this defect. So is
-- the overlay's prior-biennium lookback (#282): a member seated 2016-12-12 whose
-- span opens on the 2017-18 floor starts that tenure in 2016, key unchanged.
--
-- The biennium's last year is its first year + 1, never its two-digit tail:
-- `1999-00` ends in 2000.
--
-- A plain `error` with no baseline: 0 on the production build 2026-09-27, so a
-- threshold would only be somewhere for a regression to hide.
{{ config(severity='error') }}
select
    entity_id,
    span_key,
    span_start_biennium,
    valid_from
from {{ ref('assignments') }}
where year(valid_from) > cast(split_part(span_start_biennium, '-', 1) as integer) + 1
