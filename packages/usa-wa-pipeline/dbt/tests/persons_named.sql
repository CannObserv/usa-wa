-- A published person's name is a NAME, or it is absent (#364).
--
-- The defect this exists for: `GetSponsors` returns a name-blanked STUB for a
-- superseded / departed (member, chamber-tenure) — a real `Id`, `Name` a single
-- space, no first/last. Survivorship read that as the member's newest
-- attestation, and Tina Orwall, Tim Sheldon, Robert Sutherland and Simon Sefzik
-- shipped `' '` as their legal name. Nothing here noticed; power-map#497 found
-- it three weeks later while building mapping models against `persons`, and
-- under the #490 contract the producer owns a person's legal name — a consumer
-- applying that naively would have asserted whitespace as four names.
--
-- `conformed.entities._name` is why no blank is built today. This is the guard
-- that makes the next one a BUILD FAILURE here rather than a discovery
-- downstream, which is what the issue asked for.
--
-- Spelled `is distinct from nullif(trim(...), '')`, which is one predicate for
-- three defects: `' '` and `''` both fold to null and mismatch a non-null name;
-- `'Marlo Braun '` (a real WSL value, as is PDC's
-- `'MICHAEL JAMES BAUMGARTNER '`) mismatches its own trimmed form; and a null
-- name matches null and passes. `is distinct from` rather than `<>` for the
-- usual reason — `NULL <> NULL` is NULL, which SQL drops, so the plain form
-- would be blind to exactly the null-side rows the pair check below is about.
--
-- The pair travels together: a name with no source, or a source with no name,
-- is a survivorship bug even when neither value is blank.
--
-- GATED AT ZERO, with no baseline. The corpus is clean as of 2026-09-10 (3,135
-- persons), so any row here is new — unlike `assignments_seat_occupancy`, which
-- ratchets because its corpus was not clean on day one.
--
-- NOT expressed as a `not_null` column test on `name_full`, nor as a `required`
-- constraint in the published datapackage (the question #364 raises): one live
-- entity legitimately has no name. WSL member 31656 — Denny Heck, Lt. Governor,
-- an ex-officio Senate Rules seat minted from the retired `committee-members:`
-- archive whose live vocabulary excludes non-legislator ex-officio members — is
-- registered, anchored in power-map's crosswalk, and attested by NO staging row
-- at all, so no source can name him (`parity_wsl.ACCEPTED` carries the same
-- acceptance for the same member). Requiring a name would wedge the nightly on
-- a documented gap; requiring that a name never be BLANK loses nothing.
--
-- Casts to varchar throughout: the hermetic build materializes `persons` empty,
-- and an empty object column can bind as something other than VARCHAR (#361).
select entity_id, name_full, name_source
from {{ ref('persons') }}
where cast(name_full as varchar)
      is distinct from nullif(trim(cast(name_full as varchar)), '')
   or (cast(name_full as varchar) is null)
      is distinct from (cast(name_source as varchar) is null)
