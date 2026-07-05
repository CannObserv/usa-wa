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
- ✅ **House** chamber **membership** Assignments — a non-seat `membership` Role on the House org
  (see PM dependency below). This captures "X is a member of the WA House this biennium" **now**; the
  precise districted **seat** (Position 1/2) is layered in later by #69. We do **not** emit a
  position-less House *seat*: PM's create path **mints** an unseeded seat tuple (confirmed
  power-map#267 — a NULL-qualifier House tuple synthesizes a title and creates a spurious third seat),
  so a title-keyed membership Role is the clean way to capture House chamber affiliation without
  polluting PM's seat graph. House members therefore get Person + party + committee memberships +
  chamber membership this cut; only their exact district-seat waits on #69.
- ✅ Committee membership Assignments (membership-only; no chair/vice — no WSL source).
- ⛔ **House** chamber **seat** (district + Position) Assignments — **deferred to #69** (WA PDC adapter
  supplies Position). This is the only House data that waits.
- ⛔ Leadership / committee position, bills (P1c), special sessions.

**PM dependencies (both filed; both degrade gracefully so P1b is not blocked):**
- **power-map#269** — a generic non-seat `membership` role_type. Committee + House-chamber membership
  Roles adopt `role_type="membership"` once it ships; **until then** they degrade to a title-keyed
  non-seat Role (`title="Member"` / `"State Representative"` on the org), which the existing `(org,
  title)` observation path already handles. No blocker — just a semantic upgrade when it lands.
- **power-map#270** — an `org_wa_party` identifier type. Party Orgs attach by that identifier once it
  ships; **until then** they attach by the org **name-match** cascade (PM's party orgs exist).

## Tradeoffs / alternatives

- **Senate-first vs all-or-nothing.** Shipping Senate seats + all Persons + party + committees now
  (House chamber seats deferred) delivers most of the value immediately and isolates the one true
  blocker (#69). Alternative — hold all chamber Assignments until #69 — needlessly delays 49 Senate
  seats that are fully resolvable today.
- **District on the seat Role, not the Person.** Per the v1.4 IA, `District` resolves to
  `Role.jurisdiction_id` (the seat), not a Person column. Consequence: a House member with no seat
  Role (deferred) carries **no district** until #69. Accepted — the alternative (a temporary Person
  district column) reintroduces the denormalization the decoupling removed.
- **Party as a separate Assignment to a Party Org** (not a Person column) — matches PM + LegiScan
  spec; party Orgs already exist in PM so the org name-match cascade attaches them.
- **Committee membership = title-keyed Role** ("Member"), not a seat — PM has no committee-member
  seat type; the existing non-seat `(org, title)` observation path handles it.

## Common gates (every code-touching step)

`uv run ruff check . && uv run ruff format --check .` clean; `uv run pytest` green with coverage
gate; new tests mirror source layout; TDD red→green per step; no inline imports; UTC/ISO 8601.

## Steps

0. **Identity-stability verification (write-free spike).** Before ingesting, confirm the member
   `Id` is a stable `Person.source_id`: (a) `GetSponsors(2025-26)` `Id` vs the `Id` returned by
   `GetActiveCommitteeMembers` for the same person on a committee, and (b) `Id` across `2023-24` →
   `2025-26` for a known re-elected member. A `probe_member_identity` CLI (talks to `WSLClient`
   directly, no runner — mirrors `probe_committee_extent`) tallies overlap. **Verifiable when:** the
   probe reports whether `Id` is stable cross-endpoint and cross-biennium; the finding is recorded in
   this plan's Revisions and picks the canonical `source_id` (default: `GetSponsors.Id`). If `Id`
   diverges cross-endpoint, the committee normalizer keys Person on `(FirstName, LastName, District)`
   name-match against the sponsor cohort instead of `Id`.

1. **Transport: sponsor + member operations (TDD + cassettes).** `transport.py` gains
   `fetch_sponsors(biennium) -> WireFetch` (SponsorService.GetSponsors) and
   `fetch_committee_members(agency, committee_name) -> WireFetch`
   (CommitteeService.GetActiveCommitteeMembers), each returning parsed records + pristine SOAP wire
   (the #54 archival contract), plus offline re-parsers (`parse_sponsors` / `parse_committee_members`)
   through the same binding (the #56 cache path). Record cassettes:
   `sponsor_service_get_sponsors_2025-26.yaml` and
   `committee_service_get_active_committee_members_<agency>_<committeeName>.yaml` (a couple of
   representative committees). **Verifiable when:** transport cassette round-trip tests pass; the
   parsed shape pins the live field names (`Party` encoding, `FirstName`/`LastName`/`LongName`,
   `District`); wire re-parse matches live parse.

2. **Party Org bootstrap (TDD).** Extend `synthesis.py` + `bootstrap.py`: synthesize the three Party
   Orgs (`Washington State Republican Party`, `Washington State Democratic Party`,
   `Washington State Independent`) as `org_type="party"`, `jurisdiction_id=usa-wa`,
   `parent=null`, keyed `source_id` `party-r` / `party-d` / `party-i`; idempotent `ON CONFLICT DO
   NOTHING`. Return them on `BootstrapAnchors`. **Verifiable when:** `test_bootstrap` asserts the 3
   party rows added alongside the existing 6 anchors, idempotent on re-run.

3. **Org descriptor: party identity (TDD).** `identifier_type_for` currently maps any non-chamber/
   non-legislature `usa_wa_legislature` org to `org_wa_legislature_committee_id` — wrong for a party.
   For `org_type="party"`: emit `identifier_type="org_wa_party"` **once power-map#270 ships**; until
   then return `None` so the cascade falls to the **name** stage, attaching our Party Org to PM's
   existing `Washington State {Republican,Democratic} Party` (verified present). **Verifiable when:** a
   party-org descriptor test asserts the party path (identifier when #270 is live, else name-match) and
   no `org_wa_legislature_committee_id` is emitted; existing org-descriptor tests stay green.

4. **Person normalizer (TDD).** `normalize/sponsors.py`: `normalize_sponsors(payload, anchors,
   jurisdiction_id) -> NormalizedBatch` emitting, per member: a **Person** (`source_id=Id`,
   `name_first=FirstName`, `name_last=LastName`, `name_full=f"{FirstName} {LastName}"`,
   `name_used=LongName` when it differs), a **`person_wa_legislature_member_id`** identifier row, and a
   **party Assignment** (`Person → Role("Member") on the party Org`, `role_type="party_member"`,
   `legislative_session_id=biennium session`, `valid_from=biennium start`). Party canonicalization maps
   `"R"`/`"Republican"` → `party-r`, `"D"`/`"Democrat"` → `party-d`, else `party-i`. **Verifiable
   when:** `test_normalize_sponsors` covers R/D/independent, name recomposition, identifier emission,
   and party-Assignment wiring against the cassette.

5. **Chamber Assignments — Senate seats + House membership (TDD).** In the sponsor normalizer,
   branch on `Agency`:
   - **Senate:** resolve `District` → `usa-wa-ld-{n}` → jurisdiction id; **get-or-create** the Senate
     seat `Role` (`org=Senate anchor`, `role_type="state_senator"`, `jurisdiction_id=LD`,
     `qualifier=NULL`, `name="State Senator"`); emit a **seat** Assignment (`Person → seat Role`,
     session-scoped, `valid_from=biennium start`, `is_active=true`).
   - **House:** get-or-create a **non-seat chamber membership** `Role` on the House anchor
     (`role_type="membership"` when power-map#269 is live, else the title-keyed degrade; `name="State
     Representative"`; **no** `jurisdiction_id`/`qualifier` — so it stays out of the seat index and is
     never a seat observation); emit a **membership** Assignment. Log `wsl_house_seat_deferred_to_69`
     at info (the precise district-seat comes with #69). Do **not** synthesize a NULL-qualifier House
     seat Role.
   **Verifiable when:** `test_normalize_sponsors` asserts a Senate member yields a `state_senator` seat
   Role keyed on its LD + a seat Assignment; a House member yields a non-seat House-membership Role +
   membership Assignment and **no** districted seat Role; Senate seat reuse (two bienniums, same LD)
   doesn't duplicate; the House membership Role is one-per-chamber (not per-member).

6. **Committee-member normalizer (TDD).** `normalize/committee_members.py`:
   `normalize_committee_members(payload, anchors, committee_org_id, jurisdiction_id) ->
   NormalizedBatch` — per member: get-or-create Person (same `source_id` rule as step 0/4), a committee
   membership **Assignment** (`Person → Role("Member") on the committee Org`, non-seat
   `role_type="membership"` when power-map#269 is live else the title-keyed degrade, session-scoped,
   `valid_from=biennium start`). No position/leadership (no WSL source). Party here is cross-checkable
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

- **Member `Id` cross-endpoint / cross-biennium stability** — resolved by step 0 before any ingest;
  the committee normalizer's Person-keying strategy depends on the finding.
- **House district gap (narrowed).** House members get Person + party + committee memberships + a
  chamber **membership** Assignment this cut; only their **district-seat** (LD + Position) waits on #69,
  since district lives on the seat `Role.jurisdiction_id`. So "no district yet" ≠ "no House data" —
  call the distinction out in the spec so a consumer doesn't misread it.
- **Party `Id`-less match.** Party Orgs attach to PM by **name** (no identifier) — a PM rename of a
  party org would break the match; low risk (party names are stable), and the #45 dated-name mirror
  brings PM's canonical back. Independent (`party-i`) may have no PM counterpart → creates new (fine).
- **`legislative_session_id` = biennium session.** Assignments scope to the biennium session row (P1a
  synthesized), not a regular/special child — consistent with the spec's "Assignment carries the
  per-biennium dimension." Confirm the biennium session id is on `BootstrapAnchors` (add if absent).
- **Assignment natural key.** `source_id` must be deterministic + stable — proposed
  `f"{member_id}:{role_source_id}:{biennium}"`; pin in step 4 to survive re-runs (LWW).
- **Intra-biennium churn is snapshot-lossy** (spec Lossy ← item 3) — membership captured per refresh;
  `valid_to` only records changes as repeated refreshes observe them. Not changing the shape.

## Revisions during execution

Captured per the writing-plans skill (Phase 4 small-revision policy). None yet.
