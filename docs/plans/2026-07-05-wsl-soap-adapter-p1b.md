# WSL SOAP adapter — P1b: sponsor / member cluster (persons, roles, assignments)

Issue: [usa-wa#27](https://github.com/CannObserv/usa-wa/issues/27). Builds on P1a (#23,
committees + synthesized anchors) and the seat-Role precursor (#68, shipped). Spec:
[`docs/specs/2026-06-18-transformation-wsl-soap.md`](../specs/2026-06-18-transformation-wsl-soap.md)
§ `canonical.persons` / `canonical.assignments`.

## Problem

usa-wa produces Organizations and synthesized sessions but **no people**. `canonical.persons`,
`canonical.roles` (occupant rows), `canonical.assignments`, and `canonical.person_identifiers`
are empty, so the identity graph PM expects (Person × Role × period) has no usa-wa contributions
and #14 (identity sync end-to-end) can't complete. P1b populates the member cluster from WSL SOAP:
current legislators, their chamber seats, party membership, and committee membership.

## Approach

Three WSL operations, one normalizer each, all through the existing `AdapterRunner`
(archive wire + provenance + idempotent upsert), keyed off the biennium the daily refresh
already computes:

- `SponsorService.GetSponsors(biennium)` → **Person** + `person_wa_legislature_member_id`
  identifier + **party Assignment** + (Senate only) **chamber seat Assignment**.
- `CommitteeService.GetActiveCommitteeMembers(agency, committeeName)` → **committee membership
  Assignment**, fanned out over the roster cohort P1a already enumerates.
- `CommitteeService.GetCommitteeMembers(biennium, agency, committeeName)` → historical membership
  (optional this cut; the current-biennium `GetActiveCommitteeMembers` is the first target).

Seat attachment rides the #68 machinery: a seat Role carries `(org, role_type, jurisdiction,
qualifier)` and the sidecar attaches it to PM's seeded seats; the role-type catalog mirror already
confirms `state_representative` / `state_senator` as seat types in prod.

**Scope of this cut:**
- ✅ Person + identifier for **all** current members (House and Senate).
- ✅ Party Assignments for all members (party Orgs exist in PM — verified).
- ✅ **Senate** chamber **seat** Assignments (1 seat/LD, `qualifier` NULL — fully resolvable from WSL).
- ✅ Committee membership Assignments for **all** members incl. House (membership-only; no chair/vice —
  no WSL source). This is the bulk of the House member cluster.
- ⛔ **House** chamber Assignment — **deferred whole to #69** (WA PDC adapter supplies Position), created
  **fresh** there, not upgraded. We do **not** create a coarse interim House chamber Assignment: a
  position-less House *seat* can't be emitted (PM's create path **mints** an unseeded structural tuple —
  power-map#267 — spawning a spurious seat), and a coarse non-jurisdictional `member` chamber Assignment
  **can't be cleanly retired** when the real seat lands — PM has no producer-facing assignment
  delete/supersede (only `is_current` soft-close), and the sync engine can't re-point an *anchored*
  assignment's `role_id` (the structural match key), so a member→seat "upgrade" would orphan the coarse
  PM assignment as `is_current=true`. Per the most-specific-data-plus-aggregation principle, the clean
  answer is **one** House chamber Assignment, created once by #69. House members still get Person + party
  + committee memberships now; their chamber affiliation is meanwhile derivable from those House committee
  memberships. (Decision + analysis: #27 thread 2026-07-06.)
- ⛔ Leadership / committee position, bills (P1c), special sessions.

**PM dependencies (both accepted + implemented — power-map#269/#270; confirm live in the catalog/API
before relying, but no graceful-degrade path is needed):**
- **power-map#269** — the generic classifier role_type shipped as slug **`member`** (display "Member",
  `expects_jurisdiction=false`), auto-exposed by `GET /api/v1/role-types`. Committee membership + party
  Roles set `role_type="member"`. PM matches these on `(organization_id, lower(title))` — `member` is a
  pure **classifier**, not a match key — so the emitter must send `role_type` (persisted on create so
  "all memberships" aggregates) **alongside** the title (see Step 3b). The mirror (usa-wa#68) picks
  `member` up on its next catalog sync.
- **power-map#270** — the `org_wa_party` identifier type shipped. **Value convention: bare lowercase
  party slug — `democratic` / `republican`** (no `wa-` prefix). PM's two party Orgs are backfilled with
  it, so our first party-Org observation AUTO_ATTACHES. **No Independent Org:** PM confirmed a party is
  just an Organization with no special modeling, so **independent = the _absence_ of a party
  Assignment** — we do not synthesize an "Independent" Org (downstream must read "no party assignment"
  as *unaffiliated*, not an error).

**Terminology (power-map#271).** PM retired the composite noun "seat" for the field vocabulary
**Role Type / Jurisdiction / Qualifier**, and renamed the catalog hint `is_seat` → `expects_jurisdiction`
(usa-wa#70 mirrored this). usa-wa keeps "seat" as internal shorthand for a jurisdictional Role
(`uq_roles_seat`, `_is_seat_role_type`) — a Role carrying `jurisdiction_id` (+ `qualifier`). This plan
uses "seat" in that local sense.

## Tradeoffs / alternatives

- **Senate-first vs all-or-nothing.** Shipping Senate seats + all Persons + party + committees now
  (House chamber Assignment deferred) delivers most of the value immediately and isolates the one true
  blocker (#69). Alternative — hold all chamber Assignments until #69 — needlessly delays 49 Senate
  seats that are fully resolvable today.
- **No coarse House chamber Assignment (upgrade-in-place is not achievable).** We considered creating a
  coarse `member` House chamber Assignment now and upgrading it to the positioned seat at #69. Rejected
  after tracing the sync: PM has no producer-facing assignment delete/supersede (only `is_current`), and
  the engine can't re-point an *anchored* assignment's `role_id`, so the coarse PM row would strand as
  `is_current=true`. One Assignment, created once by #69, is the faithful reading of most-specific +
  no-duplication. Consequence: a House member carries **no district** (it lives on the seat) until #69 —
  accepted; district is not put on the Person (that reintroduces the denormalization the decoupling
  removed).
- **Party as a separate Assignment to a Party Org** (not a Person column) — matches PM + LegiScan
  spec; party Orgs exist in PM and attach by the `org_wa_party` identifier.
- **Committee membership = `member` role_type** (power-map#269), matched on `(org, title)` — not a
  jurisdictional seat. PM has no committee-member seat type; the classifier makes memberships
  aggregatable without inventing a title vocabulary.

## Common gates (every code-touching step)

`uv run ruff check . && uv run ruff format --check .` clean; `uv run pytest` green with coverage
gate; new tests mirror source layout; TDD red→green per step; no inline imports; UTC/ISO 8601.

## Steps

0. **Identity-stability verification (write-free spike). ✅ DONE 2026-07-06 — `Id` is stable.**
   Confirmed the member `Id` is a stable `Person.source_id`: (a) `GetSponsors(2025-26)` `Id` vs the
   `Id` returned by `GetActiveCommitteeMembers` for the same person on a committee, and (b) `Id`
   across `2023-24` → `2025-26` for re-elected members. The `probe_member_identity` CLI (talks to
   `WSLClient` directly, no runner — mirrors `probe_committee_extent`) tallied overlap: **cross-endpoint
   94/94 same `Id`, cross-biennium 125/125 same `Id`, 0 divergences either way** (see Revisions).
   **Canonical `source_id` = `GetSponsors.Id`**; the committee normalizer keys Person on `Id` directly
   (the `(FirstName, LastName, District)` name-match fallback is not needed — dropped).

1. **Transport: sponsor + member operations (TDD + cassettes). ✅ DONE 2026-07-06.** `transport.py`
   gained `fetch_sponsors(biennium) -> WireFetch` (SponsorService.GetSponsors) and
   `fetch_committee_members(agency, committee_name) -> WireFetch`
   (CommitteeService.GetActiveCommitteeMembers), each returning parsed records + pristine SOAP wire
   (the #54 archival contract), plus offline re-parsers (`parse_sponsors` / `parse_committee_members`)
   through the same binding (the #56 cache path). Also added the non-archival parsed-dict siblings
   `get_sponsors` / `get_active_committee_members` in step 0 (the probe's pulls). Recorded cassettes:
   `sponsor_service_get_sponsors_2025-26.yaml` (158 rows), plus one House + one Senate committee —
   `committee_service_get_active_committee_members_house_appropriations.yaml` (31),
   `committee_service_get_active_committee_members_senate_ways_and_means.yaml` (24). **Verified:**
   12 cassette round-trip tests pass on pure replay; they pin the live field names and the
   per-endpoint `Party` split (sponsor `R`/`D` vs committee `Democrat`/`Republican`), the
   153-named/5-blanked sponsor split, and prove each offline re-parse recovers the live parse.

2. **Party Org bootstrap (TDD).** Extend `synthesis.py` + `bootstrap.py`: synthesize **two** Party Orgs
   — `Washington State Republican Party` / `Washington State Democratic Party` — as `org_type="party"`,
   `jurisdiction_id=usa-wa`, `parent=null`, keyed `source_id` `party-republican` / `party-democratic`;
   idempotent `ON CONFLICT DO NOTHING`. **No Independent Org** (power-map#270: independent = absence of a
   party Assignment). Return them on `BootstrapAnchors`. **Verifiable when:** `test_bootstrap` asserts
   the 2 party rows added alongside the existing 6 anchors, idempotent on re-run.

3. **Org descriptor: party identity (TDD).** `identifier_type_for` currently maps any non-chamber/
   non-legislature `usa_wa_legislature` org to `org_wa_legislature_committee_id` — wrong for a party.
   For `org_type="party"`: emit `identifier_type="org_wa_party"`, `identifier_value` = the bare party
   slug (`republican` / `democratic`, power-map#270). PM's party Orgs are backfilled with this
   identifier, so the observation AUTO_ATTACHES. **Verifiable when:** a party-org descriptor test
   asserts `org_wa_party` + the correct value is emitted (and **not** `org_wa_legislature_committee_id`);
   existing org-descriptor tests stay green.

3b. **Descriptor: emit `role_type` for catalog-known non-seat roles (TDD).** The #68 `to_observation`
   sends `role_type` only for seats; a `member` Role would land in PM with a NULL `role_type_id` (the
   classifier lost). Extend it: emit `role_type=row.role_type` whenever the local `role_type` is a
   catalog-known slug — for a seat that's alongside `jurisdiction_id`/`qualifier` (no title); for a
   non-jurisdictional `member` role it's **alongside the title** (PM still matches on `(org, title)`,
   power-map#269). **Verifiable when:** `test_role_descriptor` asserts a `member` role emits
   `{organization_id, title, role_type:"member"}` and a non-catalog role stays title-only.

4. **Person normalizer (TDD).** `normalize/sponsors.py`: `normalize_sponsors(payload, anchors,
   jurisdiction_id) -> NormalizedBatch` emitting, per member: a **Person** (`source_id=Id`,
   `name_first=FirstName`, `name_last=LastName`, `name_full=f"{FirstName} {LastName}"`,
   `name_used=LongName` when it differs), a **`person_wa_legislature_member_id`** identifier row, and a
   **party Assignment** (`Person → Role("Member", role_type="member") on the party Org`,
   `legislative_session_id=biennium session`, `valid_from=biennium start`) — **only when the member has
   a major-party affiliation**. Party canonicalization maps `"R"`/`"Republican"` → `party-republican`,
   `"D"`/`"Democrat"` → `party-democratic`, else (independent / blank) → **no party Assignment**.
   **Iterate rows, not members, and filter to persons first** (`FirstName` and `LastName` both
   present, reuse the probe's `is_person`): `GetSponsors` returns **one row per (member,
   chamber-tenure)** and mixes in name-blanked stubs (departed members + superseded prior-chamber
   tenures — step 0 finding). Then **dedup Person by `Id`** — a mid-biennium chamber mover (e.g. Emily
   Alvarado `34024`) has **two named rows** sharing the `Id`; key Person on `Id` so they collapse to
   one human (attributes identical). `Id` is stable across the chamber change.
   **Verifiable when:** `test_normalize_sponsors` covers R/D + an independent (no party Assignment),
   a name-blanked stub (skipped), a two-named-row mover (one Person, deduped by `Id`), name
   recomposition, identifier emission, and party-Assignment wiring against the cassette.

5. **Senate seat Assignments (TDD).** In the sponsor normalizer, branch on `Agency` **per row** (a
   mid-biennium chamber mover contributes a House row *and* a Senate row under one `Id` — Step 4):
   - **Senate:** resolve `District` → `usa-wa-ld-{n}` → jurisdiction id; **get-or-create** the Senate
     seat `Role` (`org=Senate anchor`, `role_type="state_senator"`, `jurisdiction_id=LD`,
     `qualifier=NULL`, `name="State Senator"`); emit a **seat** Assignment (`Person → seat Role`,
     session-scoped, `valid_from=biennium start`, `is_active=true`).
   - **House:** emit **no chamber Assignment or chamber Role** — the House chamber Assignment is #69's
     alone (created fresh; see § "#69 hand-off"). Log `wsl_house_chamber_deferred_to_69` at info. House
     members still get Person + party (Step 4) + committee memberships (Step 6). Do **not** synthesize a
     NULL-qualifier House seat Role, and do **not** create a coarse `member` House chamber Role.
   **Verifiable when:** `test_normalize_sponsors` asserts a Senate member yields a `state_senator` seat
   Role keyed on its LD + a seat Assignment; a **House member yields Person + party but no chamber Role
   or chamber Assignment**; a mid-biennium mover (House + Senate rows, one `Id`) yields one Person + a
   Senate seat Assignment (House row → none); Senate seat reuse (two bienniums, same LD) doesn't duplicate.

6. **Committee-member normalizer (TDD).** `normalize/committee_members.py`:
   `normalize_committee_members(payload, anchors, committee_org_id, jurisdiction_id) ->
   NormalizedBatch` — per member: get-or-create Person keyed on `Id` (`source_id=Id`, stable
   cross-endpoint per step 0 — no name-match), a committee
   membership **Assignment** (`Person → Role("Member", role_type="member") on the committee Org`,
   power-map#269, session-scoped, `valid_from=biennium start`). No position/leadership (no WSL source).
   Party here is cross-checkable
   but the sponsor pull is authority. **Verifiable when:** `test_normalize_committee_members` asserts
   membership Assignments against a committee cassette, dedupes a member already created by the sponsor
   pull, and skips no-position gracefully.

7. **Adapter dispatch + fan-out (TDD).** Extend `WALegislatureAdapter`: `discover` yields
   `sponsors:<biennium>` and, per roster committee, `committee-members:<agency>:<committeeName>`;
   `fetch_one` routes by resource-id prefix to the matching transport call (archives wire);
   `normalize` routes by resource-id/service to the matching normalizer. Committee-member fan-out is
   **sequential** over the `CommitteeRosterCohortProvider` cohort (do-not-parallelize-against-WSL,
   `--pause-seconds` like the harvesters). **Verifiable when:** `test_adapter_with_runner` drives the
   runner over cassette-backed transport and asserts Person / identifier / Assignment / seat-Role rows
   + one FetchEvent/RawPayload per resource; re-run is a cache hit.

8. **Wire into `refresh.py` (TDD).** The daily refresh, after committees, pulls
   `GetSponsors(current biennium)` (forced past the TTL like the meeting window, #63) and fans out
   `GetActiveCommitteeMembers` over the current roster — `fill_only=True` (#65): additive discovery,
   never clobber PM-curated rows. Non-current `USA_WA_BIENNIUM` runs stay cache-governed. **Verifiable
   when:** `test_refresh_e2e` (integration, live WSL) asserts Persons + Senate seat Roles + party +
   committee Assignments materialize with a valid FK chain; the sidecar then attaches seats/persons
   (manual check against PM, or a follow-up).

9. **Spec + docs.** Flesh the spec's `canonical.persons` / `canonical.assignments` sections from
   sketch to full correspondence tables (party canonicalization, seat resolution, session scoping,
   House-deferral note); update `AGENTS.md` adapter layout (new normalizers, sponsor/member resources)
   and `docs/COMMANDS.md`. **Verifiable when:** spec tables match the shipped normalizers; AGENTS.md
   layout lists the new modules.

## Open questions / risks

- **Member `Id` cross-endpoint / cross-biennium stability** — ✅ **resolved by step 0 (2026-07-06):
  `Id` is stable on both axes** (94/94 cross-endpoint, 125/125 cross-biennium, 0 divergences). The
  committee normalizer keys Person on `GetSponsors.Id` directly; no name-match fallback.
- **House chamber gap.** House members get Person + party + committee memberships this cut; the whole
  **chamber Assignment** (the seat, which carries district + Position) is #69's, created fresh — no
  interim coarse Assignment (see Tradeoffs for why upgrade-in-place isn't achievable). So "no chamber
  Assignment yet" ≠ "no House data" (party + committees are here); a consumer needing chamber affiliation
  before #69 derives it from the member's WA House committee memberships. Call this out in the spec.
- **Party attach — now identifier-based** (power-map#270). Party Orgs attach by the `org_wa_party`
  identifier (`republican` / `democratic`), which PM backfilled onto its two party Orgs — deterministic
  AUTO_ATTACH, no name-match fragility. Independent is modeled as the *absence* of a party Assignment
  (no Org), so there is no independent-Org attach to worry about.
- **Positionless-seat guard is coming (power-map#269 follow-up).** PM will add `requires_qualifier`
  (`state_representative`=TRUE, `state_senator`=FALSE) and reject a jurisdictional create missing
  `qualifier` → `qualifier_required`. Confirms our approach — we never emit a positionless House seat
  (P1b emits no House chamber Role at all); when the guard lands it's belt-and-braces for #69.
- **`legislative_session_id` = biennium session.** Assignments scope to the biennium session row (P1a
  synthesized), not a regular/special child — consistent with the spec's "Assignment carries the
  per-biennium dimension." Confirm the biennium session id is on `BootstrapAnchors` (add if absent).
- **Assignment natural key.** `source_id` must be deterministic + stable —
  `f"{member_id}:{dimension}:{biennium}"` where `dimension` ∈ {`chamber-senate`, `party`,
  `committee:{committee_source_id}`}. Keep the role a *value* of the Assignment, not part of the key
  (general hygiene — an Assignment's role can be corrected without a new row). Pin in step 4/5.

- **#69 hand-off: House chamber Assignment is created FRESH, not upgraded.** P1b deliberately produces
  **no** House chamber Assignment (see Tradeoffs). #69 creates the seat Assignment from scratch: a new
  unanchored row → `sweep_unanchored` → CREATE on PM. There is nothing to retire, no orphan, no anchored
  `role_id` re-point (which the sync engine cannot do anyway). This is *why* P1b holds off — a coarse
  interim Assignment could not be cleanly retired via the producer API. #69 resolves District + Position
  → the `state_representative` seat Role + Assignment, exactly as Senate does here.
- **Intra-biennium churn is snapshot-lossy** (spec Lossy ← item 3) — membership captured per refresh;
  `valid_to` only records changes as repeated refreshes observe them. Not changing the shape.

## Revisions during execution

Captured per the writing-plans skill (Phase 4 small-revision policy).

- **2026-07-06 — Step 0 finding: member `Id` is a stable `Person.source_id` (both axes).**
  Ran the write-free `probe_member_identity` CLI against live WSL (`2025-26` vs `2023-24`,
  12 active committees sampled):
  - **Cross-endpoint** (`GetSponsors` vs `GetActiveCommitteeMembers`): 94 matched by name,
    **94 same `Id`, 0 divergent, 0 committee members absent from the sponsor cohort**.
  - **Cross-biennium** (`GetSponsors(2025-26)` vs `GetSponsors(2023-24)`): 125 matched, **125
    same `Id`, 0 divergent** (26 only-2025 / 25 only-2023 = ordinary election churn).
  → **Canonical `source_id` = `GetSponsors.Id`** (the int as a string). The committee
  normalizer (Step 6) keys `Person` on `Id` **directly** — the `(FirstName, LastName,
  District)` name-match fallback in Step 0 is **not needed** and is dropped. Resolves the
  first open question.
- **2026-07-06 — `GetSponsors` returns one row per (member, chamber-tenure), not per member
  (investigated the 5 "non-person" rows).** The blanked rows are **not** institutional/committee
  sponsors (earlier mislabel) — every one carries a real, stable member `Id` and a chamber-typed
  `LongName` with the **name stripped** (`"Representative "` / `"Senator "`). Resolving the 5 Ids in
  `2023-24` showed two mechanisms:
  - **Prior-chamber stubs of still-serving members** (Orwall `14205` House→Senate, Slatter `27504`) —
    the same `Id` **also** appears as a *named* current Senate row; the House stub is the superseded
    tenure, name-blanked.
  - **Departed members** (Hawkins `2006`, Sam Hunt `5155`, Rivers `15814`) — the blanked stub is the
    **only** 2025-26 row (no named counterpart); they left but still hold biennium sponsorships.

  And the multi-row-per-`Id` shape extends to **named** rows: `Id` `34024` (Emily Alvarado) and
  `35410` (Victoria Hunt) each appear **twice, both named** (House + Senate, same District/Party) —
  mid-biennium House→Senate movers whose *both* tenures fall inside 2025-26. So 153 named rows =
  **151 distinct members**. **`Id` is stable across a chamber change** (Orwall/Slatter/Alvarado/Hunt
  all keep their `Id`), reinforcing the source_id verdict on a third axis. Consequences for Step 4/5:
  1. **Iterate rows, not members**; **filter `is_person`** (drops the 5 blanked stubs → the 3 departed
     yield no Person, correct; the 2 boundary-movers survive via their named Senate row).
  2. **Dedup Person by `Id`** — a mid-biennium mover has 2 named rows sharing the `Id`; keying Person on
     `Id` collapses them to one human (attributes identical, no conflict).
  3. **Seat Assignment is per-row (per chamber tenure)** — a mover's Senate row → a Senate seat
     Assignment; their House row → nothing (House deferred to #69). Falls out of the `Agency` branch
     *provided* the loop is over rows.
  4. **`GetSponsors` = "sponsored a bill this biennium" ⊇ currently-seated members** (151 > 147 seats,
     from mid-biennium replacements). Acceptable for the identity graph; the snapshot-lossy `valid_to`
     caveat covers the tenure imprecision.
- **2026-07-06 — Party is spelled differently per endpoint.** `GetSponsors` uses single letters
  `"R"` / `"D"`; `GetActiveCommitteeMembers` uses full words `"Republican"` / `"Democrat"`. Party
  canonicalization (Step 4) must accept **both** forms → `party-republican` / `party-democratic` (the
  plan already mapped both; this confirms the full-word form is load-bearing, not defensive). A
  null/blank party (independent, or a blanked stub) yields **no party Assignment**.
- **2026-07-06 — Transport gained the two non-archival member pulls now** (`get_sponsors`,
  `get_active_committee_members`), the parsed-dict siblings the probe calls — mirroring
  `get_committees`. Step 1 still adds the **archival** `fetch_sponsors` / `fetch_committee_members`
  (wire + `#54`) and the offline re-parsers + cassettes.

- **2026-07-06 — Steps 2–8 shipped** (commits `d65b83d`, `18949bd`, `f2ffe7b`). Three design
  decisions the plan under-specified, resolved during execution:
  1. **Session-aware member normalizers (intra-batch FK resolution).** The `AdapterRunner` upserts
     each entity by natural key independently and reads its id back — it **cannot** resolve an
     intra-batch FK, so an `Assignment` needs a *real* `person_id`/`role_id` before it is written
     (no existing normalizer had this — committees/meetings emit only Orgs). Resolution:
     `normalize/members.py` `get_or_create_person` / `get_or_create_role` **SELECT-or-INSERT against
     the session** (flushing a new row for its id); the normalizers build Assignments with those ids;
     the runner then re-upserts each returned entity idempotently (ON CONFLICT) and writes its
     Citation. The adapter therefore carries the runner's `session` (a `_require_session()` guard),
     a deliberate departure from the pure-`normalize(payload)` contract, scoped to the member
     normalizers. Leaf rows (`PersonIdentifier`/`Assignment`) are keyed deterministically and just
     built.
  2. **Explicit-drive, not `discover`, for the member resources** (mirrors the #39 meeting window).
     `discover` still yields only `committees:<biennium>`; `refresh._discover_members` drives
     `sponsors:<biennium>` + the committee-members fan-out **forced past the TTL for the date-current
     biennium** (#63) — so the daily pull is deterministic, matching how the meeting window is driven.
  3. **DB-enumerated fan-out roster.** The committee-members fan-out enumerates the
     `org_type='committee'` rows keyed to a House/Senate chamber **from the DB** (materialized by the
     committees phase) rather than a second `GetActiveCommittees` pull — no extra SOAP call, correct
     on a committees cache-hit, and the meeting-derived Joint/`Other` class (chamber = legislature) is
     excluded (GetActiveCommitteeMembers only covers House/Senate committees).
  Also confirmed: a local `PersonIdentifier` row (scheme `wa_legislature_member_id`) **is** created
  per the spec (2026-06-18), alongside `Person.source_id` = the member `Id` (the person descriptor
  emits `person_wa_legislature_member_id` to PM from `source_id`; the child row is the queryable
  N-scheme graph P1c bill sponsorships join on). Both derive from one WSL field in one pass, so they
  can't drift.
