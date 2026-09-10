-- A published person's name is a NAME, or it is absent (#364).
--
-- The defect this exists for: `GetSponsors` returns a name-blanked STUB for a
-- superseded / departed (member, chamber-tenure) — a real `Id`, `Name` a single
-- space, no first/last. Survivorship read that as the member's newest
-- attestation, and Tina Orwall, Tim Sheldon, Robert Sutherland and Simon Sefzik
-- shipped `' '` as their legal name. Nothing here noticed; power-map#497 found
-- it a week later while building mapping models against `persons` — the
-- snapshot it read was `v20260903T085702Z`, the report came 2026-09-10 — and
-- under the #490 contract the producer owns a person's legal name — a consumer
-- applying that naively would have asserted whitespace as four names.
--
-- `conformed.entities._name` is why no blank is built today. This is the guard
-- that makes the next one a BUILD FAILURE here rather than a discovery
-- downstream, which is what the issue asked for.
--
-- Spelled `is distinct from nullif(trim(...), WS)`, which is one predicate for
-- three defects: `' '` and `''` both fold to null and mismatch a non-null name;
-- `'Marlo Braun '` (a real WSL value, as is PDC's
-- `'MICHAEL JAMES BAUMGARTNER '`) mismatches its own trimmed form; and a null
-- name matches null and passes. `is distinct from` rather than `<>` for the
-- usual reason — `NULL <> NULL` is NULL, which SQL drops, so the plain form
-- would be blind to exactly the null-side rows the pair check below is about.
--
-- WS is spelled out because duckdb's ONE-ARGUMENT `trim` strips SPACES ONLY
-- (CR 2): `trim(chr(9) || 'a' || chr(9))` returns the tabs untouched, so a
-- tab- or newline-padded name walked past the first cut of this gate. Python's
-- `str.strip()` — what `entities._name` applies — strips every whitespace
-- class, so such a name cannot come from the survivorship at all. It could only
-- come from some OTHER writer into `persons`, which is precisely the case a
-- gate exists for; one blind to everything but the defect already fixed
-- upstream is not a guard.
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
--
-- The character set is written out inline rather than hoisted into a jinja
-- variable, for two reasons that both bite. `test_persons_named` runs this file
-- with jinja stripped and refuses outright on any expression tag it does not
-- understand — deliberately, so the unit half can never quietly exercise a
-- mangled query; it knows `ref`, not a hand-rolled name. And dbt parses jinja
-- inside SQL COMMENTS too, so even naming the tag syntax here failed the whole
-- project's compile until this sentence stopped spelling it out.
select entity_id, name_full, name_source
from {{ ref('persons') }}
where cast(name_full as varchar) is distinct from nullif(
          trim(cast(name_full as varchar), ' ' || chr(9) || chr(10) || chr(13)), ''
      )
   or (cast(name_full as varchar) is null)
      is distinct from (cast(name_source as varchar) is null)
