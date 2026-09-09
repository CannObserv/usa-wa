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
-- MULTI-MEMBER DISTRICTS are excluded, because they are not conflicts (#360).
-- Washington's 1889 legislature seated multi-member senate districts — 35
-- senators across 24 districts, LD-19 alone carrying five — so `seat:senate:ld-N`
-- collapses genuinely distinct seats into one role_key and this gate would read
-- five lawful senators as a five-way fight.
--
-- The roster draws the distinction itself, which is what makes capacity
-- checkable rather than guessed: of 156 district-years with more than one senate
-- row, 151 carry succession annotations ("Resigned January 13, 1997", "Deceased
-- Aug. 9, 1971") and 5 are bare — all 1889. An annotated extra row is a
-- SUCCESSOR within one seat; a bare extra row is a SEAT.
--
-- The reading is deliberately conservative: ANY annotation in a district-year
-- means capacity 1. Treating a partly-annotated year as multi-member would
-- license exactly the conflict this gate exists to catch, so ambiguity resolves
-- toward policing rather than excusing.
--
-- NOT keyed on the roster's `order` column, which looks like a seat index and is
-- not: it is alphabetical by surname within a district-year, and 101 of 417
-- people change order between bienniums. Keying on it would mint seat identities
-- that never existed, make ordinary succession look like seat-hopping, and move
-- historical `role_key` values — power-map's seat match key.
--
-- BASELINE 71 — the corpus is not clean, and `error` would wedge the nightly
-- chain on day one. The residue after multi-member districts are excluded, and
-- it is NOT one shape (#360): ~53 are successions whose dated boundary exists in
-- the roster but could not be applied because the annotation carries no day
-- ("Appointed Oct. 1971" — 60 of 1,046 annotations are month-only, 69 year-only,
-- 173 undated), and ~18 have no roster explanation at all and need case-by-case
-- adjudication. This test exists to stop the count GROWING while that is worked,
-- which is the guard #358 showed was absent.
--
-- The ratchet, in dbt's own semantics rather than a hand-rolled one:
--   >BASELINE  error — a new conflict; the thing this test exists to catch
--   !=BASELINE warn  — including FEWER: the baseline is stale, ratchet it down
-- so a fix that removes a conflict is as loud as a regression that adds one.
--
-- The baseline is mode-aware, and that is load-bearing rather than tidy. The
-- hermetic build materializes conformed models EMPTY on purpose, so its correct
-- expectation is 0 — with a flat 91 the gate warned `Got 0 results` on every
-- pre-commit and every CI run. That noise costs the ratchet its whole point:
-- the day the real count drops to 85, "ratchet me down" would arrive looking
-- exactly like the warning everyone had already learned to scroll past. Alert
-- fatigue is how #49 alerting dies, and a gate that cries wolf in the inner
-- loop is the fastest route to it.
--
-- Known weakness, stated rather than papered over: a count baseline can mask
-- one new conflict behind one repaired elsewhere. Acceptable while the set is
-- being actively drained in #360; if that stalls, the upgrade is a named-pair
-- baseline in the `parity_wsl.ACCEPTED` idiom.
{% set baseline = 0 if env_var('USA_WA_PIPELINE_HERMETIC', '0') == '1' else 71 %}
{{ config(severity='error', error_if='>' ~ baseline, warn_if='!=' ~ baseline) }}
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
  and not exists (
      select 1
      from {{ ref('stg_roster_members') }} r
      where cast(r.chamber as varchar) = regexp_extract(cast(a.role_key as varchar), 'seat:(\w+):', 1)
        and cast(r.district as varchar) = regexp_extract(cast(a.role_key as varchar), 'ld-(\d+)', 1)
      group by r.chamber, r.district, r.year
      -- `year` is a grouping key, so min(r.year) below is identity on it — the
      -- aggregate is only there to be legal in HAVING (CR 128)
      having count(*) > 1
         and count(r.annotation) = 0
         -- the multi-member biennium must cover the overlap, not merely exist
         -- casts throughout: the hermetic build types an empty `assignments`
         -- INTEGER, so a bare DATE comparison fails to BIND there (#361)
         and make_date(cast(min(r.year) as integer) + 1, 12, 31)
             >= greatest(cast(a.valid_from as date), cast(b.valid_from as date))
         and (
             (a.valid_to is null and b.valid_to is null)
             or make_date(cast(min(r.year) as integer), 1, 1) <= least(
                 coalesce(cast(a.valid_to as date), cast(b.valid_to as date)),
                 coalesce(cast(b.valid_to as date), cast(a.valid_to as date))
             )
         )
  )
