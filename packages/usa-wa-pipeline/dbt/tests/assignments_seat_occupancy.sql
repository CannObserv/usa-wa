-- A seat holds ONE person at a time (#359). Two distinct entities whose tenures
-- overlap on the same seat is either bad upstream data or a span the builder
-- failed to clip — both publishable today, because nothing else checks it.
--
-- Nothing else does, despite appearances: `assignments_key` tests span IDENTITY
-- (one tenure start per entity), which two different holders of one seat pass
-- cleanly, and the daily `succession-invariants` gate scopes to `is_active`
-- rows — the CURRENT cohort only. History has never been gated, and the tier
-- that gate reads retires in #314.
--
-- Scoped to `seat:*` roles via span_kind. Party membership and committee seats
-- are legitimately multi-holder, so the constraint is stated by KIND rather
-- than by an exception list that would rot. One caveat if House coverage ever
-- deepens past 1965: pre-1965 House seats were fungible at-large, two per
-- district with no Position (usa-wa#101), so a position-less `seat:house:ld-N`
-- would legitimately carry two concurrent holders and must be excluded here.
-- Every `chamber-house` span today carries `position-N`, so none exist yet.
--
-- A shared boundary date is a handoff, not an overlap: a span ending the day
-- its successor begins is how a mid-term succession is spelled, and counting it
-- would bury the real conflicts under hundreds of clean transitions. Spelled
-- `is distinct from` rather than `<>`: an open span's `valid_to` is NULL, and
-- `NOT (false OR NULL)` is NULL, which SQL drops — so the plain form made the
-- gate BLIND to every conflict involving a currently-serving member, the live
-- ones that matter most. Caught by `test_an_open_span_still_conflicts`.
--
-- An open span (`valid_to is null`) means "still serving", so it overlaps
-- anything that starts after it. Spelled as an explicit null branch rather than
-- `coalesce(valid_to, date '9999-12-31')`: besides the sentinel being a smell,
-- the hermetic build materializes an empty `assignments` with an INTEGER
-- `valid_to`, and coalescing that against a DATE literal fails to bind at all.
--
-- BASELINE 91 — the corpus is not clean, and `error` would wedge the nightly
-- chain on day one. All 91 are succession-boundary artifacts between genuinely
-- different people (zero are the same person under two entity ids, so none are
-- matching failures): a successor floored to the biennium start instead of the
-- seating date (Herman D. Crow 1897-01-01 where H. E. Houghton left 1897-08-25),
-- or a predecessor run to the biennium ceiling instead of the departure date.
-- They are catalogued in #360; this test exists to stop the count GROWING while
-- that is worked, which is the guard #358 showed was absent.
--
-- The ratchet, in dbt's own semantics rather than a hand-rolled one:
--   >91  error — a new conflict; the thing this test exists to catch
--   !=91 warn  — including FEWER than 91: the baseline is stale, ratchet it down
-- so a fix that removes a conflict is as loud as a regression that adds one.
--
-- Known weakness, stated rather than papered over: a count baseline can mask
-- one new conflict behind one repaired elsewhere. Acceptable while the set is
-- being actively drained in #360; if that stalls, the upgrade is a named-pair
-- baseline in the `parity_wsl.ACCEPTED` idiom.
{{ config(severity='error', error_if='>91', warn_if='!=91') }}
select
    a.role_key,
    a.entity_id as entity_a,
    a.valid_from as from_a,
    a.valid_to as to_a,
    b.entity_id as entity_b,
    b.valid_from as from_b,
    b.valid_to as to_b
from {{ ref('assignments') }} a
join {{ ref('assignments') }} b
    on a.role_key = b.role_key
    and a.entity_id < b.entity_id
where a.span_kind in ('chamber-senate', 'chamber-house')
  and b.span_kind in ('chamber-senate', 'chamber-house')
  and (b.valid_to is null or a.valid_from <= b.valid_to)
  and (a.valid_to is null or b.valid_from <= a.valid_to)
  and a.valid_to is distinct from b.valid_from
  and b.valid_to is distinct from a.valid_from
