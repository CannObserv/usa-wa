# WA PDC adapter — House member District + Position (seat binding for #27)

Issue: [usa-wa#69](https://github.com/CannObserv/usa-wa/issues/69). Builds on P1b
([#27](https://github.com/CannObserv/usa-wa/issues/27); Persons, party, Senate seats, committee
memberships) and the seat-Role model (#68). Discovery findings + resolved open questions:
[#69 comment 2026-07-07](https://github.com/CannObserv/usa-wa/issues/69#issuecomment-4905363231).

## Problem

WSL exposes a House member's `District` but **not** their **Position** (1 / 2) — the ballot construct
that distinguishes the two representatives per LD. Power Map's seat-Role model keys a House seat on
`(org, state_representative, jurisdiction=LD, qualifier="Position 1"/"Position 2")`, so P1b **could not**
emit House chamber Assignments (only Senate, which is 1/LD, qualifier NULL). Position lives in
election/candidate sources. This adapter sources Position from the **WA PDC** and emits the House seat
Assignment #27 deferred — created **fresh** (per the corrected #69 hand-off: nothing to retire, no
orphan, no anchored `role_id` re-point).

## Approach

**One Socrata dataset, read as a Position resolver.** The PDC `Campaign Finance Summary` dataset
(`3h9x-7bvm`) on data.wa.gov (SODA/REST/JSON) carries, per candidacy: `filer_name`, `person_id` (stable
PDC id), `office`, `position`, `legislative_district`, `party_code`, `general_election_status`,
`election_year`, `updated_at`. A single filtered SoQL GET returns exactly the seated House cohort:

```
GET https://data.wa.gov/resource/3h9x-7bvm.json
  ?office=STATE REPRESENTATIVE
  &election_year=<biennium_start - 1>
  &$where=general_election_status='Won in general'
```

→ ~2 winners × 49 LDs, each a resolved `(LD, position, party, name, pdc_person_id)`.

**PDC is a Position resolver, not a Person source — and the cross-link is deterministic.** The House
seat Assignment attaches to the WSL-`Id`-keyed `Person` P1b already created; we do **not** mint a PDC
Person. The key enabler (discovered during design): PM's person-observation request already accepts
`additional_identifiers` (an explicit "attach these identifier claims to the resolved entity" list), and
our client passes it straight through. So a `person_wa_pdc` identifier can be attached to the *same* PM
person the WSL primary identifier (`person_wa_legislature_member_id`) already resolves — deterministically,
with **no reliance on PM name-match** and **no duplicate Person** locally or on PM. Flow:

1. Build the WSL House `(LD, last-name) → member Id` map from a `GetSponsors(biennium)` pull (House rows
   carry `District`; districts aren't stored locally post-decoupling — the WSL client is a package dep).
2. Within each LD, match the (≤2) PDC winners ↔ the (≤2) WSL House members by **folded last name**
   (+ party tiebreak). Winner filter ⇒ each `(LD, position)` is exactly one person, so the set is tiny.
   Use a local name fold (a Layer-3 adapter must not import the Layer-4 sidecar's `normalize_name`).
3. On a match, against the **existing WSL Person** (`source=usa_wa_legislature`): add a `person_wa_pdc`
   child `PersonIdentifier` row (value = PDC `person_id`) **and** emit the House seat Assignment
   `(person → state_representative seat Role, qualifier=Position N)`. An unmatched winner (appointee /
   name miss) is logged, no seat.

**Descriptor enhancement (Layer 4, small + general):** `PersonDescriptor.to_observation` currently emits
only the single `Person.source`-derived primary identifier. Extend it to also emit the Person's child
`person_identifiers` rows (those whose scheme maps to a PM slug other than the primary) as
`additional_identifiers`. This is the mechanism that carries `person_wa_pdc` onto the WSL-anchored PM
person; it generalizes the N-scheme identifier graph P1b built to the PM write path. Not a PM gap — PM
was already ready (`additional_identifiers` + admin `/identifiers` primitive).

Seat attachment rides #68: get-or-create the `state_representative` seat Role
`(org=House anchor, role_type=state_representative, jurisdiction_id=LD, qualifier="Position N")`; map PDC
`position` `"1"`/`"2"` → `"Position 1"`/`"Position 2"` (power-map#263 vocabulary). Fresh unanchored
Assignment → `sweep_unanchored` → CREATE on PM (mirrors P1b Senate).

Provenance via the runner: archive the raw SODA JSON response as `RawPayload`, `content_hash` derived by
the runner (#54). New **httpx** transport (REST/JSON) — distinct from the zeep SOAP `WSLClient`, but
mirrors the `WireFetch` (raw bytes + parsed) contract so #54 hashing is identical.

## Scope (initial cut)

- ✅ New `usa-wa-adapter-pdc` package (Layer 3), `source_slug = "usa_wa_pdc"`.
- ✅ httpx SODA transport → `Campaign Finance Summary` (`3h9x-7bvm`), filtered to seated House winners.
- ✅ Match PDC winners ↔ existing WSL House Persons (within-LD, folded last name + party; WSL districts
  from a `GetSponsors` pull).
- ✅ `person_wa_pdc` child `PersonIdentifier` on the matched WSL Person + `PersonDescriptor` emits it as
  a PM `additional_identifier` (deterministic cross-link to the WSL-anchored PM person).
- ✅ House `state_representative` seat Role + Assignment (`qualifier="Position N"`), created fresh.
- ✅ Provenance spine (Source/FetchEvent/RawPayload); respx/vcrpy cassette + TDD.
- ⛔ Lobbying / contributions / expenditures (the rest of the PDC cluster — later issues; same dataset
  family, so this is the first slice).
- ⛔ Senate (WSL resolves it fully — P1b).
- ⛔ Historical backfill of positions across bienniums (current biennium only; `position` populated
  ~2018+; gated on per-biennium WSL member cluster — clean follow-up).
- ⛔ Candidate-vs-incumbent nuance beyond current seated members.

## Tradeoffs / alternatives

- **Resolver vs Person source.** Matching to the existing WSL Person (not minting a PDC Person) avoids a
  dual-Person reconciliation problem — one human, one `Person`, cross-linked by two identifiers
  (`wa_legislature_member_id` + `person_wa_pdc`). Cost: an appointed / mid-term-replacement member with
  no PDC winner row gets no seat (log unresolved, don't fabricate) — same lossy posture as P1b.
- **Winner filter (`general_election_status='Won in general'`)** collapses the many-candidates-per-race
  rows to the one seated person per `(LD, position)`. Alternative (read all candidates, dedup) needlessly
  ingests losers this cut doesn't need.
- **Within-LD name match** is robust because the winner set per LD is ≤2; party disambiguates. Messy
  `filer_name` (`"JACOBSEN CYNTHIA P (Cyndy Jacobsen)"`) is absorbed by last-name normalization.
- **App token optional** (rate-limiting only, not auth): wire `USA_WA_PDC_APP_TOKEN` sent as
  `X-App-Token` only when set. Not needed for correctness at once-daily single-GET volume.

## Common gates (every code-touching step)

`uv run ruff check . && uv run ruff format --check .` clean; `uv run pytest` green with coverage gate;
new tests mirror source layout; TDD red→green per step; no inline imports; UTC / ISO 8601.

## Steps

0. **Write-free discovery probe (codify the curl findings).** `probe_pdc_house.py` (mirrors
   `probe_member_identity` — talks to the SODA endpoint directly, **no runner**): pulls the seated House
   winner cohort for a biennium, tallies LDs covered / positions present / winners missing position, and
   dry-matches against WSL House Persons in the DB to report the match rate + unresolved names. Answers
   "how clean is the join + is every seat covered" before the ingest path. **Verifiable when:** run
   against live PDC + local DB prints a coverage/match table.

1. **Package scaffold + SODA transport (TDD + cassette).** New `packages/usa-wa-adapter-pdc/`
   (pyproject, workspace member, `src/usa_wa_adapter_pdc/`). `transport.py`: `PDCClient` (httpx) with
   `fetch_house_winners(election_year) -> WireFetch` (raw JSON bytes + parsed rows) and an offline
   re-parser (the #56 cache path). App token via optional `X-App-Token`. **Verifiable when:** a
   respx/vcrpy cassette round-trip test replays a recorded response and pins the field names +
   winner/position shape.

2. **`PersonDescriptor` emits child identifiers as `additional_identifiers` (TDD, Layer 4).** Extend
   `to_observation` to append the Person's child `person_identifiers` rows — each mapped scheme → PM slug
   (`wa_pdc` → `person_wa_pdc`; skip the one already sent as the primary) — as `additional_identifiers`
   (`{identifier_type_slug, identifier_value}`). Add a `scheme → slug` map alongside `identifier_type_for`.
   **Verifiable when:** `test_person_descriptor` asserts a WSL Person carrying a `wa_pdc` child row emits
   `additional_identifiers=[{person_wa_pdc, <pdc id>}]` while the primary stays
   `person_wa_legislature_member_id`; a Person with no child rows emits none. This is the mechanism the
   whole cut hangs on — do it first so the emit path is proven before the adapter produces the rows.

3. **Person-identifier + seat helpers (TDD).** `normalize/positions.py`: `canonical_position(raw)` →
   `"Position N"`; `house_seat_role_source_id(ld, position)`; `build_pdc_person_identifier(person, row)`
   (scheme `wa_pdc`, value = PDC `person_id`); `fold_last_name(name)` (local casefold+unaccent+strip — not
   the Layer-4 `normalize_name`); reuse `ld_slug` / `resolve_ld_jurisdiction` from the WSL member helpers
   (import, don't duplicate). **Verifiable when:** unit tests cover position mapping, deterministic
   source_ids, identifier shape, name folding.

4. **Match + normalize (TDD, session-aware).** `normalize/house_positions.py`:
   `normalize_house_positions(payload, wsl_house_roster, anchors, session) -> NormalizedBatch`, where
   `wsl_house_roster` is the `(LD, folded-last-name) → member Id` map from `GetSponsors`. For each PDC
   winner: resolve its WSL member Id within its LD by folded last name (+ party tiebreak), `SELECT` the
   existing WSL `Person` by `(usa_wa_legislature, member Id)`, then emit a `person_wa_pdc`
   `PersonIdentifier` (child of that Person) + get-or-create `state_representative` seat Role + House seat
   Assignment (`source_id=f"{wsl_member_id}:chamber-house:{biennium}"`, symmetric with P1b's
   `chamber-senate`). Logs `pdc_house_unresolved` for a winner with no WSL/Person match (appointee /
   name-mismatch / not-yet-ingested). **Verifiable when:** `test_normalize_house_positions` covers a clean
   2-rep LD (both positions), a party-disambiguated pair, an unresolved winner (logged, no seat/identifier),
   and seat-Role reuse.

5. **Adapter + runner wiring (TDD).** `PDCAdapter(BaseAdapter)`: `discover` yields
   `house-winners:<election_year>`; `fetch_one` → transport (archives wire); `normalize` → the House
   position normalizer, fed the `GetSponsors`-derived WSL House roster map (built once per run via the
   WSL client). Session-aware like the WSL member adapter (`_require_session`). Source bootstrap:
   `_get_or_create_source(slug="usa_wa_pdc", kind="rest")`. **Verifiable when:** `test_adapter_with_runner`
   drives the runner over a cassette + a seeded WSL House Person and asserts identifier + seat Role +
   Assignment rows + one FetchEvent/RawPayload; re-run is a cache hit.

6. **Deploy wiring — daily refresh CLI (TDD).** `refresh.py`
   (`python -m usa_wa_adapter_pdc.refresh`): resolve current biennium → `election_year = start - 1`,
   pull House winners, `fill_only=True` + `skip_unchanged=True` (mirrors P1b forced pulls). Systemd:
   a `usa-wa-pdc-refresh.{service,timer}` staggered off the WSL refresh (so WSL House Persons exist
   first), `OnFailure=` alert (#49), unit-ordering test (#52). **Verifiable when:** `test_refresh`
   asserts the biennium→year mapping + fill-only wiring; the unit passes `verify-units`.

7. **Spec + docs.** Add a PDC adapter section to the transformation spec (or a new
   `docs/specs/2026-07-07-transformation-wa-pdc.md`): dataset, filter, join key, position→qualifier,
   House-seat correspondence. Update `AGENTS.md` project layout (new package) + `docs/COMMANDS.md`
   (probe + refresh CLIs). **Verifiable when:** docs match the shipped normalizers.

## Open questions / risks

- **Source bootstrap.** New `Source(slug="usa_wa_pdc", kind="rest")` — mirror `_get_or_create_source`
  from the WSL adapter (resolve during step 5).
- **Cross-source identifier cross-link → RESOLVED (not a PM gap).** PM's person-observation request
  already accepts `additional_identifiers`, and our client passes it through, so `person_wa_pdc` attaches
  to the WSL-anchored PM person deterministically (step 2). No name-match dependency; one Person, one PM
  person.
- **WSL House district sourcing → re-pull `GetSponsors`.** A House member's district isn't stored locally
  (decoupling; lives on a seat we don't yet have). The PDC refresh builds the `(LD, last-name) → member
  Id` map from a `GetSponsors(biennium)` pull via the WSL client (a package dep) — WSL is the district
  authority, PDC supplies only Position.
- **Appointees / replacements** have no PDC winner row → no seat (logged). Accept the gap.
- **Name ambiguity** within an LD (shared last name) — rare; party + position disambiguate. Log a
  multi-match as unresolved rather than guess.
- **`requires_qualifier` guard** (power-map#273/#71) is already enforced for `state_representative` —
  PDC supplies the qualifier, so belt-and-braces, no positionless emit.
- **Assignment natural key** `f"{wsl_member_id}:chamber-house:{biennium}"` — role is a *value*, keeping
  the key role-independent (P1b hygiene).

## Revisions during execution

- **2026-07-07 — Discovery confirmed one dataset covers the cut.** `Campaign Finance Summary`
  (`3h9x-7bvm`) filtered to `office=STATE REPRESENTATIVE ∧ general_election_status='Won in general'`
  returns **98 rows = 49 LDs × 2 positions** for election year 2024 (full House coverage); `person_id` is
  stable across years (Barkis `64`, 2016→2026). Cassette recorded live.
- **2026-07-07 — Cross-source identifier cross-link is NOT a PM gap.** PM's `people/observations` already
  accepts `additional_identifiers` and our client passes it through (`post_observation` → `from_dict`);
  `PersonDescriptor.to_observation` was the only missing piece. Propagation to the anchored cohort rides
  the existing enrich-payload **fingerprint drift** (`additional_identifiers` is in the hashed payload), so
  a newly-added `wa_pdc` child re-triggers enrich with no `needs_enrich` change — the mechanism designed
  for "a newly-added carry field reaching the existing cohort."
- **2026-07-07 — Name match is a token-set test, not surname extraction.** PDC `filer_name` is too
  inconsistent (`"JACOBSEN CYNTHIA P (Cyndy Jacobsen)"`, `"J.T. Wilcox (JT Wilcox)"`) to pick "the"
  surname; instead the WSL member's clean `LastName` is tested for membership in the winner's folded
  token set — robust within an LD's ≤2 winners.
- **2026-07-07 — Seat Role source is `usa_wa_legislature`, not `usa_wa_pdc`.** A seat is legislature
  structure; reusing the WSL `get_or_create_role` keeps House seats symmetric with Senate seats (both
  `usa_wa_legislature`, matching PM's structural seat). Only the PDC-provenance rows (identifier +
  assignment) carry `usa_wa_pdc`. The runner reads `entity.source` as-is (no override), so the mixed-source
  batch upserts each by its own `(source, source_id)`.
- **2026-07-07 — Steps 1–7 shipped, TDD, all green** (39 new package tests; full suite 957 passed, 97%
  coverage). New `usa-wa-pdc-refresh.{service,timer}` (06:30 UTC, ordered after WSL refresh); unit-ordering
  test (#52) + AGENTS.md/COMMANDS.md updated.
- **2026-07-07 — Follow-up [#74](https://github.com/CannObserv/usa-wa/issues/74): mid-biennium replacement
  inference.** The first prod run left 2/98 House seats unresolved — winners (Slatter LD48, Orwall LD33)
  who moved to the Senate mid-biennium. `house_positions` gained a second reconciliation pass: within-LD
  elimination assigns the leftover roster member (the appointed replacement) to the leftover position when
  the deferred winner reappears as that LD's Senator (the confirming signal). The mover's PDC identity is
  also cross-linked onto their current (Senate) Person. Design + verification: the #74 issue thread.
