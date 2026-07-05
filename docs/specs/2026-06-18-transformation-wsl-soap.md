# Transformation: WSL SOAP web services → usa-wa hybrid legislative IA

- **Date:** 2026-06-18
- **Status:** draft (transformation #4 of 4 in the cohort; feeds adapter implementation P1a/b/c)
- **Direction:** **WSL SOAP → ours, only.** WSL is the producer; we never publish back. (Power Map is the archival store for the Person / Organization / Role / Assignment cluster; the sidecar handles that direction, not WSL.)
- **Scope:** Field-level mapping between [`canonical.*` entities](2026-05-27-hybrid-legislative-ia.md) and the SOAP/XML envelopes exposed by `https://wslwebservices.leg.wa.gov/` — Washington State Legislature's nine ASMX web services (`AmendmentService`, `CommitteeActionService`, `CommitteeMeetingService`, `CommitteeService`, `LegislationService`, `LegislativeDocumentService`, `RcwCiteAffectedService`, `SessionLawService`, `SponsorService`). WA Legislature, biennium 2025–26 forward (historical backfills out of scope).
- **Inputs:** hybrid IA v1.4 (this repo, [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)); jurisdictional IA design ([`docs/specs/2026-05-31-jurisdictional-ia-design.md`](2026-05-31-jurisdictional-ia-design.md)); sibling transformation specs (LegiScan / OCD / uscongress) as templates.
- **Outputs:** per-entity correspondence tables; vocabulary mappings; lossy-direction inventory; vocab/schema deltas required to receive WSL data cleanly.
- **Non-goals:** legislative-document text content (P3); Code Reviser identifier parsing (existing `document_identifiers` table covers storage); GovInfo XML mimetype handling; pre-2025 historical backfill; consumption of WSL's non-SOAP surfaces (web pages, calendars, agency reports).

## Empirical validation (2026-06-24)

The draft's `Committee`-shape assumptions were checked against live WSL
(`CommitteeService` / `SponsorService`, bienniums 2019-20 → 2025-26). Findings,
applied inline below:

- **`Id` is the stable committee key — not `Acronym`.** `Id` is identical across
  2021-22 / 2023-24 / 2025-26 for every carried-over committee (APP=31634,
  CB=31635, TR=31651, HCW=31644; 0 of 30 shared committees re-keyed 2023-24→2025-26).
  **`Acronym` *and* `LongName` change on a stable `Id`** (Id 29195: `BFGT`
  "Business, Financial Services, Gaming & Trade" → `BTE` "Business, Trade &
  Economic Development"; 5 such renames 2023-24→2025-26). `source_id=Id` is
  correct; the proposed PM-layer "fold cross-biennium rows by shared `Acronym`"
  continuity mechanism is **unsound** (acronyms are not stable) and is dropped.
  A renumbering occurred at the 2019→2021 boundary (Commerce & Gaming 20900→31639),
  so `Id` stability holds only within the in-scope modern era.
- **`GetCommittees(biennium)` ≈ `GetActiveCommittees`** — both return the same
  flat list (34 for 2025-26: 19 House + 15 Senate). Neither exposes a richer set.
- **No Joint committees in any biennium** of `CommitteeService` (`GetCommittees`
  checked 1991-92 → 2025-26: every row is `Agency=House`/`Senate`). WA's joint
  bodies are a **separate class reachable only through
  `CommitteeMeetingService.GetCommitteeMeetings(beginDate, endDate)`**, where each
  meeting carries `Agency` ∈ {`House`, `Senate`, `Joint`, `Other`} and a nested
  `Committees.Committee[]` list (`Id, Name, LongName, Agency, Acronym, Phone`).
  Persistent statutory joint/agency bodies recur every biennium — JTC (`Id=-140`),
  JLARC (`-5`), Select Committee on Pension Policy, Pension Funding Council,
  Veterans' & Military Affairs, Joint Committee on Employment Relations (27992),
  **Joint Committee on Energy Supply, Energy Conservation, and Energy Resilience
  (Id 13945, `ESEC`, RCW 44.39)** — alongside many transient task forces. The
  id-space is heterogeneous (small/negative sentinels for standing statutory bodies,
  large positives for task forces), `LongName` is double-prefixed with the agency
  ("Joint Joint Committee on…"), and a body appears only if it *met* in the window.
  Bringing joint committees into the org graph is therefore its own cut
  (meeting-derived dedup, or synthesis of the statutory core from RCW), not a tweak
  to the `GetActiveCommittees` normalizer. The `Agency="Joint"` → legislature mapping
  in the current normalizer is dead against `CommitteeService` data but is the right
  shape for that future cut.
- **Subcommittees appear (rarely) as peer rows**, detectable only by "Subcommittee"
  in `LongName` (2019-20 & 2021-22: Id 29190 "Senate Committee on Behavioral Health
  Subcommittee to Health & Long Term Care", Agency=Senate). None in 2023-24/2025-26.
  No structural parent field — the parent committee is named inside `LongName`.
- **Committees retire.** Id 31639 (2021-22 "Commerce & Gaming" → 2023-24 "Regulated
  Substances & Gaming") is absent in 2025-26. Retirement is detected by per-biennium
  presence diffing (an explicit `GetCommittees(biennium)` membership diff), not exposed
  as a field — drives the producer `active=false` path (#44, see Lossy ← item 8), distinct
  from PM-curated `archived_at` (`LifecycleMixin`, #38/#42).
- **The `Committee` element carries exactly `Id, Name, LongName, Agency, Acronym,
  Phone`** — the field is `Phone`, not `PhoneNumber`. `GetActiveCommittees` takes
  **no** biennium argument (implicit current biennium); `GetCommittees(biennium)` is
  the parameterized historical form.
- **`LongName`, `Acronym`, and active-status are biennium-scoped, not timeless.** The
  single durable Org row (keyed on `Id`) keeps only the latest values — see new
  Lossy ← items 7–8 and Open Q 5.

## Why this exists

WSL SOAP is the **authoritative source** for everything WA-legislative — bills, amendments, sponsors, committee actions, hearings, votes, session laws. Every other WA legislative data source (OpenStates, LegiScan WA scrape, third-party aggregators) is downstream of WSL with varying lag and fidelity. The adapter at [`packages/usa-wa-adapter-legislature/`](../../packages/usa-wa-adapter-legislature/) ingests WSL SOAP directly so canonical-table writes carry first-party provenance, not third-party derivations.

This spec exists because three of the four cohort transformations (LegiScan / OCD / uscongress) are already documented; the WA-primary source — the one usa-wa was built for — was not. A WSL-side spec also reveals where WSL's shape diverges from our canonical model (it does, particularly around the `LegislativeSession` boundary and Member identity) and pins down the synthesis/derivation decisions made during the WSL adapter brainstorm (2026-06-17/18).

## WSL SOAP service inventory

Each service is an ASMX endpoint with one WSDL per service. Methods scoped per-biennium (`"2025-26"` style string) or per time window. There is **no global `GetBienniums` or `GetSessions` endpoint** — the biennium is a parameter, not a discoverable resource.

| Service | Primary operations | Maps to (canonical) |
|---|---|---|
| `LegislationService` | `GetLegislationByYear`, `GetLegislation`, `GetCurrentStatus`, `GetHearings`, `GetLegislationTypes`, `GetRollCalls`, `GetSponsors`, `GetSessionLawChapter`, `GetLegislationGovernorSigned`/`GovernorVeto`/`GovernorPartialVeto`/`PassedLegislature`/`PassedHouse`/`PassedSenate`/… (33 ops total) | `Bill`, `BillAction`, `BillSponsorship`, `BillVersion`, `VoteEvent`, `Amendment`-references |
| `AmendmentService` | `GetAmendments` | `Amendment` |
| `SponsorService` | `GetSponsors`, `GetHouseSponsors`, `GetSenateSponsors`, `GetRequesters` | `Person`, `PersonIdentifier`, `Assignment` (chamber membership) |
| `CommitteeService` | `GetActiveCommittees`, `GetCommittees`, `GetActiveCommitteeMembers`, `GetCommitteeMembers`, `GetHouseCommittees`, `GetSenateCommittees` | `Organization` (committee), `Role`, `Assignment` (committee membership) |
| `CommitteeActionService` | `GetCommitteeReferralsByBill`, `GetDoPassByCommittee`, `GetCommitteeExecutiveActionsByBill`, … (15 ops; per-committee/per-bill scoping) | `BillAction` (committee phase), `VoteCount` (in-committee votes) |
| `CommitteeMeetingService` | `GetCommitteeMeetings`, `GetCommitteeMeetingItems`, `GetRevisedCommitteeMeetings` | `BillEvent` (hearing), `BillAction` (executive session items); **also the only source of `Joint`/`Other` committee `Organization` rows** (see § Empirical validation + Lossy ← item 5) |
| `LegislativeDocumentService` | `GetDocuments`, `GetDocumentsByClass`, `GetAllDocumentsByClass`, `GetDocumentClasses` | `BillVersion`, `BillSupplement` (analysis, report, fiscal note, summary), `document_identifiers` |
| `SessionLawService` | `GetBillByChapterNumber`, `GetChapterNumbersByYear`, `GetSessionLawByBill`, `GetSessionLawByBillId`, `GetSessionLawByInitiativeNumber` | `Bill.enacted_as`, `bill_statute_changes` |
| `RcwCiteAffectedService` | `GetRcwCitesAffected` | `bill_statutory_citations` |

**P1a (first cut) services in scope:** `CommitteeService` (one op: `GetActiveCommittees`). All others are sketched in this spec for future cuts; their field mappings are placeholders until the implementation lands.

## Schema-level orientation

**Biennium as the scope unit.** Every WSL operation that returns more than one row takes a biennium string. The biennium is the closest WSL has to a "session container" — within a biennium, WA holds annual Regular Sessions (mid-Jan to late-April or sine-die) plus zero or more Special Sessions called by the Governor. WSL does not encode the Regular-vs-Special distinction at the SOAP level; we derive it from calendar conventions and synthesize the `LegislativeSession` rows.

**SOAP envelope shape.** Most operations return a typed list element under the SOAP body — e.g., `GetActiveCommittees` returns an `ArrayOfCommittee` containing zero or more `Committee` elements with the fields `Id`, `Name`, `LongName`, `Acronym`, `Agency` (`House` / `Senate`; `Joint` is documented but never observed), `Phone`. SOAP fault envelopes (`<soap:Fault>`) appear for transport-level failures (bad biennium parameter, service down). Per-operation shapes vary; the adapter's normalize layer handles each one specifically. Field names confirmed against live WSL (see § Empirical validation).

**Identifier conventions:**
- **Committee:** `Id` (a stable numeric surrogate, e.g. `31635` for House Capital Budget) is the WSL-stable committee identifier and is used as `Organization.source_id`. `Acronym` (`CB`, `WAYS`) is a *display* attribute, **not** a stable key — it changes across bienniums on a fixed `Id` (see § Empirical validation). `Id` is stable across the in-scope modern bienniums (2021-22→2025-26); a historical renumbering at the 2019→2021 boundary puts pre-2021 backfill out of the stability guarantee.
- **Bill:** `BillId` (e.g., `HB 1234`) is the human-facing identifier; `BillNumber` (the integer 1234) is the chamber-scoped numeric. We compose `source_id = f"{bill_type}-{number}-{biennium}"` (e.g., `HB-1234-2025-26`) for stable cross-biennium uniqueness.
- **Member / Sponsor:** WSL `LongName` (e.g., `Riccelli`) and `Id` (numeric, possibly `MemberId`). We use the numeric ID as `Person.source_id`.

**No first-party "since" filter.** Most operations are biennium-scoped lists; the discover-since pattern uses `GetLegislationIntroducedSince(date)` and `GetLegislationStatusChanges(date)` for change-detection windows. The adapter's `discover(since)` parameter feeds those windowed operations once we move past the committee-only first cut.

## First-cut scope (P1a — adapter foundation)

The first cut exists to prove the SOAP transport end-to-end with a small but meaningful entity production. After `python -m usa_wa_adapter_legislature.refresh`:

| Source | Row count | Tables |
|---|---|---|
| Synthesized | 1 | `canonical.organizations` (WA Legislature, `org_type="legislature"`) |
| Synthesized | 2 | `canonical.organizations` (House + Senate chambers, parent = legislature) |
| Synthesized | 1 | `canonical.legislative_sessions` (biennium row, `classification="biennium"`, parent = null) |
| Synthesized | 2 | `canonical.legislative_sessions` (2025 Regular + 2026 Regular, `classification="regular"`, parent = biennium row) |
| Live SOAP via `CommitteeService.GetActiveCommittees()` (no biennium arg — implicit current) | 34 (19 House + 15 Senate; no Joint observed) | `canonical.organizations` (committees, parent = House / Senate; `acronym` + `phone` columns populated) |

Synthesis vs. fetch is decided per-entity (see § Per-entity correspondence below). The split reflects WSL's actual contract: WSL has no `GetBienniums` or `GetSessions` endpoint, so we synthesize the structure; WSL does have first-class committee data, so we fetch.

**Closes:** `usa-wa-adapter-legislature` transitions from a 39-line stub to a working adapter. Partially closes [usa-wa#14](https://github.com/CannObserv/usa-wa/issues/14) (Organization rows produced; Person / Role / Assignment still pending future cuts).

**Defers:** Person / Role / Assignment / Bill / Amendment / Vote / hearing / session-law / RCW-affected. Each of those becomes a future cut with its own per-entity correspondence section added below.

## Vocabulary + schema additions

The first cut needs the following adds to the canonical model:

1. **`canonical.organizations.org_type` vocab gains `legislature`** — for the top-level legislature Org under which both chambers nest. Application-level docstring update only; `org_type` is `text(32)` with no DB CHECK constraint (per the project pattern). Federal `usa-fed-congress` and other state legislatures (Nebraska's Unicameral as `chamber` + `legislature` overlap) slot in cleanly.

2. **`canonical.legislative_sessions.classification` vocab gains `biennium`** — for the parent row in the biennium ↔ session hierarchy. Same docstring-level treatment.

3. **`canonical.legislative_sessions.parent_legislative_session_id` self-FK** — new ULID nullable FK to `canonical.legislative_sessions.id` with an `ix_canonical_legislative_sessions_parent_id` index, `ondelete="RESTRICT"`. New alembic migration on top of `f5f1bd9f84ae`.

   The hierarchy: biennium rows have `parent=null`; Regular / Special session rows reference their biennium. Bills FK their introducing session via `bill.legislative_session_id`; the biennium is traversed via `session.parent`. Carry-over bills (introduced in Regular 2025, alive in Regular 2026) still cross sessions within a biennium correctly because both Regular rows share the same parent biennium row.

   Cross-jurisdictional flexibility: jurisdictions without a biennium-level container (some states with annual sessions; some unicameral states) leave `parent_legislative_session_id=null`. Jurisdictions with deeper hierarchies (e.g., a future "Congress 119" → "session 1" / "session 2" mapping for `usa-fed`) use the same self-FK shape.

   The existing `biennium_label: text(16) nullable` column stays as a denormalized fast-filter (avoids a parent-row join for "all bills from biennium 2025–26"). The two representations agree by construction.

4. **`canonical.organizations.acronym` text nullable** — new column (varchar(64)) holding the current short acronym (e.g., `"CB"` for Capital Budget, `"WAYS"` for Senate Ways & Means). Single denormalized column rather than reusing `canonical.organization_identifiers` because: (a) acronym is semantically a label/display attribute, not a third-party identifier; (b) WSL exposes one acronym per Committee element; (c) it is **not stable across bienniums** (changes on a fixed `Id` — see § Empirical validation), so it must never serve as a join/match key. The sidecar's `to_observation` emits `org_acronyms: [row.acronym]` (a single-element list) to satisfy PM's [`OrganizationObservationRequest.org_acronyms: list[str]`](../../packages/powermap-client/powermap_client/models/organization_observation_request.py) shape — as a display label only. (The earlier plan to use `acronym` as PM's cross-biennium fold key is dropped: `Id` is stable, so each committee is already a single durable Org row; there is no cross-biennium duplication to fold.)

5. **`canonical.organizations.phone` text nullable** — new column (varchar(64)) holding a primary phone number (e.g., committee staff phone from `Committee.Phone`). Single denormalized column rather than a 1:N `organization_contact_methods` table because: (a) WSL exposes one phone per committee; (b) the sidecar's `to_observation` wraps it as `contact_methods: [{contact_type: "phone", value: row.phone}]` per PM's [`ObservationContactMethod`](../../packages/powermap-client/powermap_client/models/observation_contact_method.py) shape; (c) future multi-method support (people with multiple emails, secondary phones) can land alongside person ingestion in P1b.

   Both columns (`acronym`, `phone`) land in the same migration alongside `parent_legislative_session_id`.

   **Sidecar follow-up:** the Organization descriptor at [`packages/usa-wa-sync-powermap/src/usa_wa_sync_powermap/descriptors/organization.py`](../../packages/usa-wa-sync-powermap/src/usa_wa_sync_powermap/descriptors/organization.py) currently emits only `identifier_type`, `identifier_value`, `names`, `jurisdiction_affiliations`, `organization_parent_id`. Extending it to emit `org_acronyms: [row.acronym]` (when non-null) and `contact_methods: [{contact_type: "phone", value: row.phone}]` (when non-null) is a small follow-up not blocking this spec's adapter work; file as a sidecar issue alongside the adapter implementation.

## Per-entity correspondence

### Universal entity shape (provenance spine)

Every canonical row written from WSL data carries `source="usa_wa_legislature"` and a stable `source_id`. The `clearinghouse_core.sources` row for this adapter is created lazily on first refresh: `slug="usa_wa_legislature"`, `kind="soap"`, `base_url="https://wslwebservices.leg.wa.gov/"`, `reliability=1.0`, `cache_ttl_days=1`. Per-fetch provenance via the runner's `FetchEvent` + `RawPayload` chain; per-row provenance via `Citation` (one per Organization tying back to the relevant `FetchEvent`).

### `canonical.organizations` (P1a — synthesized + fetched)

The first cut writes Organization rows in two flavors: synthesized anchors (legislature + chambers) and SOAP-fetched committees.

#### Synthesized anchors

| usa-wa column | Synthesis rule | Notes |
|---|---|---|
| `source` | constant `usa_wa_legislature` | Same source-of-truth as the fetched rows; synthesis is treated as a derivation of WSL's biennium contract. |
| `source_id` | constant per anchor: `"legislature"`, `"house"`, `"senate"` | Stable across runs; bootstrap upserts on `(jurisdiction_id, source, source_id)`. |
| `jurisdiction_id` | `usa-wa` jurisdictions row (FK) | Looked up by slug at bootstrap time. |
| `name` | constant: `"Washington State Legislature"`, `"Washington State House of Representatives"`, `"Washington State Senate"` | Drawn from official style. |
| `short_name` | `null` for legislature; `"House"` / `"Senate"` for chambers | |
| `org_type` | `legislature` / `chamber` / `chamber` | New vocab value `legislature` (see § Vocabulary additions). |
| `parent_organization_id` | `null` for legislature; legislature row's ID for House + Senate | Self-referential parent. |
| `powermap_organization_id` | `null` | Set after sidecar match. |

#### Fetched committees — `CommitteeService.GetActiveCommittees(biennium)`

WSL `Committee` element fields (observed shape):

```xml
<Committee>
  <Id>31635</Id>
  <Name>Capital Budget</Name>
  <LongName>House Committee on Capital Budget</LongName>
  <Agency>House</Agency>
  <Acronym>CB</Acronym>
  <Phone>(360) 786-7100</Phone>
</Committee>
```

| usa-wa column | WSL field | Direction | Transform | Notes |
|---|---|---|---|---|
| `source_id` | `Id` | ← | passthrough (string) | WSL committee identifier — a **stable** numeric surrogate (verified identical across 2021-22→2025-26). P1a writes one durable row per `Id`; the resolved target (Open Q 5) mints a new name-epoch Org on rename and retains `Id` as the lineage link — an implementation change tracked separately. (See § Empirical validation: `Id` stable, `Acronym`/`LongName` not; the earlier "per-biennium re-keying / PM acronym-folding" plan is dropped.) |
| `name` | `LongName` | ← | direct | E.g., `"House Committee on Capital Budget"`. Changes across bienniums on a stable `Id`. P1a overwrites latest-wins (Lossy ← item 7); resolved target (Open Q 5) treats a rename as a new Org + retirement of the prior. |
| `short_name` | `Name` | ← | direct | E.g., `"Capital Budget"`. |
| `acronym` | `Acronym` | ← | passthrough (uppercase, latest-wins) | New column (see § Vocabulary additions item 4). E.g., `"CB"`, `"WAYS"`. **Not stable** across bienniums — display attribute only, never a join/match key. Sidecar emits as `org_acronyms: [acronym]` in `to_observation`. |
| `org_type` | (always `committee`) | → adapter | constant | Subcommittees would use `subcommittee` — but they appear only rarely, as peer rows detectable by "Subcommittee" in `LongName` (none in 2023-24/2025-26); deferred to P1b (see Lossy ← item 4). |
| `parent_organization_id` | `Agency` | ← | `"House"` → House Org id; `"Senate"` → Senate Org id; `"Joint"` → legislature Org id (defensive only — no Joint committee observed in any biennium) | The synthesized anchor IDs are passed into the adapter via a `BootstrapAnchors` dataclass so normalize can resolve `Agency` text → parent FK. |
| `jurisdiction_id` | (always `usa-wa`) | → adapter | constant | Same FK as the anchors. |
| `phone` | `Phone` | ← | direct (strip whitespace) | New column (see § Vocabulary additions item 5). Sidecar wraps as `contact_methods: [{contact_type: "phone", value: phone}]` in `to_observation`. |
| `powermap_organization_id` | `null` | — | | Set after sidecar match. |

**Natural-key UNIQUE on the Organization row:** `(jurisdiction_id, source, source_id)` per the canonical convention.

### `canonical.legislative_sessions` (P1a — synthesized)

All sessions in the first cut are synthesized. WSL has no first-class session entity; the biennium parameter is the only WSL-visible scoping. The synthesis encodes WA's calendar convention.

| usa-wa column | Synthesis rule (biennium row) | Synthesis rule (regular row) | Notes |
|---|---|---|---|
| `source` | `usa_wa_legislature` | `usa_wa_legislature` | |
| `source_id` | biennium string: `"2025-26"` | `f"{year}-regular"`: `"2025-regular"`, `"2026-regular"` | Stable composition; specials add `f"{year}-special-{n}"` (P1b+). |
| `slug` | `f"usa-wa-{biennium}"`: `"usa-wa-2025-26"` | `f"usa-wa-{year}-regular"`: `"usa-wa-2025-regular"`, `"usa-wa-2026-regular"` | Per [hybrid-IA spec § sessions.py](2026-05-27-hybrid-legislative-ia.md). Slug encodes `Jurisdiction.slug`, not the ULID FK. |
| `name` | `"2025-26 Biennium"` | `"2025 Regular Session"` / `"2026 Regular Session"` | Display strings. |
| `classification` | `biennium` (new vocab) | `regular` | See § Vocabulary additions. |
| `organization_id` | legislature Org id | legislature Org id | Both biennium and regular sessions belong to the legislature Org (not a chamber). |
| `parent_legislative_session_id` | `null` | biennium row's id | New self-FK (see § Vocabulary additions). |
| `start_date` | Jan 14 of first biennium year (next-Monday-after-Jan-9 convention) | Jan 13/12 (varies by year; second Monday) | Synthesis from biennium string; close-enough approximations until WSL exposes session dates somewhere. |
| `end_date` | Jan of next biennium first year | sine-die date if known; else null | First cut leaves regular end-dates null (sine-die comes from WSL elsewhere, P1b+). |
| `is_active` | computed: `true` if `now < end_date` | computed: same | Operator can override; not load-bearing for the first cut. |
| `biennium_label` | `"2025-26"` (denorm) | `"2025-26"` (denorm) | Fast-filter alongside the parent traversal. |

**Natural-key UNIQUE:** `(source, source_id)` (post-jurisdiction-decoupling shape — see [`docs/specs/2026-06-09-canonical-jurisdiction-decoupling-design.md`](2026-06-09-canonical-jurisdiction-decoupling-design.md)).

### `canonical.persons` (P1b)

**Resolved 2026-07-05** (plan: [`docs/plans/2026-07-05-wsl-soap-adapter-p1b.md`](../plans/2026-07-05-wsl-soap-adapter-p1b.md); precursor #68 shipped). Person `source_id` = the WSL member `Id` from `GetSponsors` (cross-endpoint/cross-biennium stability verified in plan step 0 before ingest). Every current member gets a Person + a `person_wa_legislature_member_id` identifier; the sidecar attaches to PM's identifier-less backfilled legislators by name, then pushes the member-id identifier back to stabilize future matches. District no longer lives on Person — it resolves to the **seat** `Role.jurisdiction_id` (see `canonical.assignments`), so a House member (seat deferred to #69) carries no district this cut.

`SponsorService.GetSponsors(biennium)` and `LegislationService.GetSponsors(bill_id, biennium)` return WSL sponsor records:

| usa-wa column | WSL field (`SponsorService.GetSponsors`) | Notes |
|---|---|---|
| `source_id` | `Id` (numeric `MemberId`) | WSL-stable per biennium; `Id` is reused across bienniums for re-elected members (verify in P1b). |
| `name_full` | `LongName` + `FirstName` | WSL splits these; we recompose. |
| `name_first` | `FirstName` | direct |
| `name_last` | `LastName` | direct |
| `name_used` | `LongName` if differs from `LastName` | E.g., LongName = `Riccelli`. |
| `gender` | (not in source) | Lossy ←. |
| `current_district` (legacy) | `District` | Now lives on `Role.jurisdiction_id` via the v1.4 IA refactor. P1b adapter resolves District → district jurisdiction slug → `Role.jurisdiction_id`. |
| `powermap_person_id` | (set by sidecar) | After sidecar match. |

External-ID schemes (`wa_legislature_member_id`, `wa_legislature_long_id`) flow to `canonical.person_identifiers` per the v1.4 hybrid IA shape.

### `canonical.assignments` (P1b)

**Resolved 2026-07-05** (plan: [`docs/plans/2026-07-05-wsl-soap-adapter-p1b.md`](../plans/2026-07-05-wsl-soap-adapter-p1b.md)). Three assignment kinds, all session-scoped to the **biennium** session row (`valid_from` = biennium start):
- **Chamber seat** — `Person → seat Role` where the seat is `(chamber org, role_type, jurisdiction=LD, qualifier)` per the #68 seat model. **Senate** (`state_senator`, 1/LD, `qualifier` NULL) ships this cut; **House** (`state_representative`, `qualifier` Position 1/2) is **deferred to #69** — WSL has no Position source and a NULL-qualifier House seat would mint a PM duplicate.
- **Party** — `Person → Role("Member")` on a synthesized Party Org. PM's `Washington State {Republican,Democratic} Party` orgs **exist** (verified 2026-07-05), so the org name-match cascade attaches ours; Independent may create-new. `Party` canonicalized across endpoints (`"R"`/`"Republican"` → `party-r`, `"D"`/`"Democrat"` → `party-d`, else `party-i`).
- **Committee membership** — `Person → Role("Member")` on the committee Org (membership-only; chair/vice has no WSL source).

WSL surfaces these in three places (signatures confirmed against live WSL 2026-06-24):

- `SponsorService.GetSponsors(biennium)` returns chamber + party for current members (`Party` as `"R"`/`"D"`).
- `CommitteeService.GetActiveCommitteeMembers(agency, committeeName)` returns *current* committee membership (no biennium arg; keyed by `agency` + committee **`Name`**, not acronym; `Party` as full word `"Democrat"`/`"Republican"`).
- `CommitteeService.GetCommitteeMembers(biennium, agency, committeeName)` returns the membership for a specified biennium.

**Assignment carries the per-biennium dimension — the committee Org does not.** A committee membership is `(Person → committee Org → Role)` scoped by `legislative_session_id` (the **biennium** session row) plus `valid_from`/`valid_to`. The committee Org is a durable name-epoch entity (Open Q 5 / #40); each biennium produces a fresh set of session-scoped Assignments pointing at the *same* Org. **Do not** mint per-biennium Org rows — that would duplicate identity and contradict PM's reuse-Org-across-biennia behavior. Mirrors how Persons work (one durable Person, N per-biennium assignments) and the LegiScan spec (chamber Assignment = `(Person, chamber_role, valid_from=biennium_start)`; committee Assignment = `(Person, committee_role, …)`; party derived as a separate Assignment to a synthesized Party Org).

Consequence: per-biennium committee **presence** falls out of the assignment layer for free — a committee with session-scoped Assignments for biennium Y existed in Y — complementing the `archived_at` tombstone (#40/#42) without a separate per-biennium participation table.

Known limits (don't change the shape):
- **No `position`/role field** in `GetActiveCommitteeMembers` — chair / vice-chair / ranking-member is *not* in SOAP; the committee-`Role` dimension needs another source (web scrape or manual curation).
- **Intra-biennium churn is snapshot-lossy** (Lossy ← item 3) — biennium membership sets are captured; `valid_from`/`valid_to` record changes only as repeated refreshes observe them.
- **`Party` encoding differs across endpoints** (`"R"`/`"D"` vs `"Democrat"`/`"Republican"`) — the normalizer must canonicalize.
- **Person `Id` may differ across endpoints** (`GetActiveCommitteeMembers` vs `GetSponsors`) — confirm the stable `Person.source_id` before ingesting either (P1b step zero).

### `canonical.bills` and related entities (P1c — sketched)

`LegislationService.GetLegislationByYear(year)` and `GetLegislation(biennium, bill_id)` return WSL bill records. Detailed mapping deferred to P1c. Sketch:

| usa-wa column | WSL field (`LegislationService.GetLegislation`) | Notes |
|---|---|---|
| `source_id` | composed: `f"{BillId.replace(' ', '-')}-{biennium}"` | E.g., `"HB-1234-2025-26"`. |
| `legislative_session_id` | derived from `OriginalAgency` + `BillId` patterns | The introducing session — biennium → regular session resolved by the bill's `IntroducedDate`. |
| `originating_chamber_id` | `OriginalAgency` → House / Senate Org id | |
| `number` | `BillNumber` | direct |
| `bill_type_id` | `LegalTitle` parsing or `LegislationService.GetLegislationTypes` lookup | The first cut seeds the lookup via a separate one-off operation (recommended P1c step zero). |
| `title` | `LongDescription` | |
| `current_status` | `CurrentStatus` | |
| `current_status_class` | derived via OCD-aligned classifier | |
| `introduced_at` | `IntroducedDate` | parse to UTC datetime |
| `enacted_as` | `SessionLawService.GetSessionLawByBillId(bill_id)` | Out-of-band sub-fetch when bill reaches enacted status. |

`BillSponsorship`, `BillAction`, `BillVersion`, `Amendment`, `VoteEvent`, `VoteCount`, `PersonVote`, `BillSupplement`, `bill_statute_changes`, `bill_statutory_citations` each get a dedicated correspondence subsection in future P1c+ updates to this spec.

## Lossy directions

### Lossy ← (WSL → ours)

1. **No session-end timestamps in WSL.** `LegislativeSession.end_date` for Regular sessions is the sine-die adjournment date; WSL doesn't expose it via SOAP (it's published on `app.leg.wa.gov` HTML pages and in agency releases). First cut leaves `end_date=null`; the sidecar pulls Power Map's record when available, or operator backfills.

2. **No party affiliation in `Committee` records.** `GetActiveCommittees` returns committee structure but not member party splits. Party flows from `SponsorService.GetSponsors` (per-member) instead — joined at query time. Not a true loss, just a join-not-denorm.

3. **No effective-date stamps on committee composition.** A committee that re-orgs mid-biennium (rare but possible) shows as a single row in `GetActiveCommittees`; we lose the "previously chaired by X, now chaired by Y" history. Power Map's bitemporal Assignment store is the right home for that history once committee memberships ingest in P1b.

4. **Subcommittees are flattened, parent inferred from name.** Subcommittees do **not** form a richer set — `GetCommittees(biennium)` returns the same flat list as `GetActiveCommittees`, and when a subcommittee exists it appears as a *peer* row with the chamber's `Agency`, not nested. They are rare (2019-20 & 2021-22: Id 29190 "Senate Committee on Behavioral Health Subcommittee to Health & Long Term Care"; **none** in 2023-24/2025-26) and carry no structural parent field — the parent committee is named inside `LongName`. P1b detection = "Subcommittee" substring in `LongName` + parent-name match (small enough to hand-curate).

5. **Joint committees are a separate, meeting-derived class.** `CommitteeService` returns **no** Joint committee in any biennium (1991-92 → 2025-26). Joint/`Other` bodies are exposed only via `CommitteeMeetingService.GetCommitteeMeetings`, as committee refs on each meeting (`Agency` ∈ {House, Senate, Joint, Other}). This is lossy/awkward as an org source: (a) a body appears only if it *met* in the queried window (dormant statutory committees vanish); (b) `LongName` is double-prefixed with the agency and must be cleaned (`Acronym` is the reliable label); (c) the id-space mixes small/negative sentinels (JTC `-140`, JLARC `-5`) with large positives (ESEC 13945, JCER 27992). The standing statutory core (RCW-authorized: JTC, JLARC, SCPP, PFC, VMA, ESEC/RCW 44.39, …) is stable enough to synthesize from statute as an alternative to meeting-scraping. Decision deferred to its own cut; the `Agency="Joint"` → legislature mapping already in the normalizer is the right shape for it.

6. **WSL `Id` stability (modern era only).** `Id` is a stable numeric surrogate across the in-scope bienniums (verified identical 2021-22→2025-26 for every carried-over committee). A renumbering occurred at the 2019→2021 boundary (Commerce & Gaming 20900→31639), so `Id` stability is **not** guaranteed across that historical break — irrelevant here (pre-2025 backfill is out of scope). `source_id=Id` therefore yields one durable Org row per committee; no PM-layer acronym-folding is needed or used (acronyms are not stable — see item 7).

7. **Name & acronym history (P1a latest-wins; resolved by Open Q 5).** `LongName` and `Acronym` change across bienniums on a stable `Id` (5 renames 2023-24→2025-26, e.g. Id 29195 `BFGT`/"Business, Financial Services, Gaming & Trade" → `BTE`/"Business, Trade & Economic Development"; Id 31639 "Commerce & Gaming"→"Regulated Substances & Gaming"). **P1a** overwrites both on each refresh. **Resolved (Open Q 5):** this latest-wins *is the correct evidence shape* — PM does **not** fork on rename (power-map#239). One durable PM Org per committee for its whole life; `org_wa_legislature_committee_id=Id` stays anchored to it (a documented PM public-API invariant). usa-wa keeps **one local row** that follows the identifier; name lineage (with effective dates) lives in PM's dated `organization_names`, not as forked Org rows.

8. **Active-in-biennium status (P1a unrepresented; resolved by Open Q 5; narrowed for #44).** A committee that exists in one biennium and not the next leaves a **P1a** durable Org row with no presence marker. **Resolved + narrowed (Open Q 5, #43/#44):** usa-wa **does** drive `active=false` on biennium-absence — but only under a deliberately narrowed condition, never from the current-only `GetActiveCommittees` pull. The original objection (current-only `GetActiveCommittees` cannot distinguish *dormant* from *abolished*) is addressed by three narrowings:
   - **Explicit-membership source, not current-only.** Detection diffs the produced committee cohort against an explicit `CommitteeService.GetCommittees(biennium)` pull (parameterized), so "absent from biennium N" is a deliberate cross-biennium diff, not an artifact of *when* the refresh ran.
   - **Cohort excludes the dormant class.** The `GetCommittees`/`GetActiveCommittees` cohort is House/Senate *standing* committees only; the statutory joint bodies the dormant-vs-abolished worry was about (JTC, JLARC, …) come from `CommitteeMeetingService`, not this endpoint, so they are not in the produced cohort. Renames keep a **stable `Id` present** (§ Empirical validation), so an absent `Id` is a genuine "no longer constituted this biennium" signal for this cohort — not a rename and not a dormant statutory body.
   - **`active` is a non-gating domain flag, not `archived`.** Since #43 added `Organization.active` (the operationally-live-vs-dissolved axis, PM-mirrored read-side, **not** a live-read hide gate), inactivation no longer means hiding the row. Setting `active=false` records the dissolution without removing the committee from reads — so a false positive is far cheaper than an `archived_at` tombstone would have been.

   **Guardrails (producer side, #44).** The transition is emitted as a one-shot producer observation (`OrganizationObservationRequest.active`, power-map#240; PM applies `active` independently of any name evidence, so an evidence-less payload is accepted) via a dedicated CLI — **not** the routine `to_observation` (which keeps `active` out, per #43 — re-asserting it every cycle would fight PM's LWW authority). The CLI reconciles **both directions**: `active=false` for committees the roster dropped, `active=true` for ones that reappear. Before emitting: (a) **completeness guard** — abort the whole run if the `GetCommittees` pull is empty/short (a partial SOAP response must not read as "everything was abolished"); (b) **cohort floor** — abort if the absent fraction exceeds a threshold of the *active* local cohort (mass absence ⇒ suspect pull, not a real mass dissolution); (c) **skip archived** — omit `active` when the local org carries `archived_at` (PM 422s `active_on_archived_org`); (d) **emit-to-PM-only** — PM stays authority for `active` and mirrors it back read-side (#43); the CLI does **not** set the local column. The floor catches *gross* partial pulls; a *modest* one (a few committees under the floor) could falsely retire — but automatic reactivation self-heals it on the next clean pull, so a transient WSL hiccup costs one cycle, not a permanent mis-mark. "Was X active in biennium Y?" remains a session-scoped Assignment-layer (P1b) question; this flag is only the durable operationally-live-vs-dissolved signal.

### Lossy → (us → WSL)

**N/A — we never publish back.** WSL is consume-only.

## Open questions

1. ✅ **Cross-biennium `Id` / `Acronym` stability.** *Resolved empirically (2026-06-24):* `Id` is the stable key (identical 2021-22→2025-26); `Acronym` and `LongName` both change on a fixed `Id`. `source_id=Id` gives one durable Org row per committee; the previously-planned PM acronym-folding is dropped as unsound. Field names confirmed: `Id, Name, LongName, Agency, Acronym, Phone` (the field is `Phone`, not `PhoneNumber`). A renumbering at the 2019→2021 boundary bounds `Id` stability to the modern (in-scope) era. See § Empirical validation + Lossy ← items 6–7.

2. ✅ **Subcommittee detection + parent linkage.** *Resolved empirically:* there is no separate subcommittee endpoint or field — `GetCommittees` returns the same flat list as `GetActiveCommittees`, and a subcommittee (when present) is a peer row with the chamber's `Agency`. Only signal is "Subcommittee" in `LongName`; parent committee is named in the same string. Rare (none in 2023-24/2025-26). *Resolution:* P1b detects by `LongName` substring + parent-name match, then sets the subcommittee row's `parent_organization_id` to the **parent committee** Org (not the chamber). PM's `OrganizationObservationRequest` exposes settable `organization_parent_id` / `organization_parent_name` / `organization_parent_acronym`, and the sidecar org descriptor already emits `organization_parent_id` from the local parent FK ([organization.py](../../packages/usa-wa-sync-powermap/src/usa_wa_sync_powermap/descriptors/organization.py) `_parent_pm_id`), so the parent link propagates to PM with no descriptor change. Hand-curate the subcommittee→parent map if the name match is unreliable. See Lossy ← item 4.

3. ✅ **Special session detection is data-driven, not operator-config.** *Resolved empirically (2026-06-24):* WSL exposes the regular/special distinction in session-law data — no operator config needed for specials that enacted law. `SessionLawService.GetChapterNumbersByYear(year)` returns a parseable `LegislativeSession` label per enacted chapter (`"2023 Regular Session"` vs `"2023 1st Special Session"`) plus `LegislatureNumber` and `Year`; and a bill's `GetCurrentStatus.Status` carries the session-law citation `C <chapter> L <yy> E<n>`, where `E<n>` marks the *n*th extraordinary (special) session (regular-session chapters have no `E` suffix). *Worked example:* `2E2SSB 5536` (the Blake fix) → `Status="C 1 L 23 E1"`, `ActionDate=2023-05-16` = Chapter 1, Laws of 2023, **1st Special Session** (the one-day special on 2023-05-16); the recap history shows the regular-session sine-die failure (2023-04-23) then the 2023-05-16 "reintroduced… Rules suspended" reconvening. *Resolution:* synthesize special-session `LegislativeSession` rows by parsing distinct `LegislativeSession` strings from `GetChapterNumbersByYear`, and bind bills to them via the `E<n>` citation; operator-supplied config is retained only as a fallback for a special that produced **no** session law (rare). Implementation lands with the session-law cut (P1c); the trigger itself is no longer an open question.

4. ✅ **`Committee.Phone` and other contact data.** Resolved — phone is in scope for P1a via the new `canonical.organizations.phone` column (see § Vocabulary additions item 5). Sidecar's `to_observation` extension to emit `contact_methods` is a separate follow-up issue but does not block the adapter implementation.

5. ✅ **Committee biennium-history modeling — one durable Org, PM-curated name timeline (no fork).** *Resolved (2026-06-24, #40 + power-map#239; an earlier draft of this answer wrongly assumed PM forks-on-rename — corrected here):* Power Map does **not** mint a new Org on rename. A WSL committee rename is **one durable PM Org for the committee's whole life**; `org_wa_legislature_committee_id=Id` stays anchored to it (a documented PM public-API invariant — per-epoch identifiers are explicitly rejected as they'd break PM's identifier-uniqueness and invert its merge-rebrands tooling). usa-wa mirrors this:
   - **Identity is the durable `Id`.** `source_id = WSL Id` (P1a) is **correct** — one local Org row per committee. The earlier "name-epoch fork / mint-new-row" plan is **dropped**: no composite natural key, no per-epoch lineage column. On rename, `rematch_anchor` re-resolving the (unchanged) identifier to the durable Org is already the right behaviour and needs no change.
   - **Name lineage lives in PM, dated.** PM is adding effective-dated `organization_names` (`effective_start`/`effective_end`, power-map#239). usa-wa's latest-wins name/acronym is correct *evidence*; PM curates the dated canonical history. P1b binds each assignment to the name-in-effect by fetching the dated list and filtering locally (no `?as_of=` endpoint needed). Until #239 ships, `upsert_from_pm` adopting a single `record["name"]` stays correct.
   - **Dissolution is producer-driven (narrowed, #44); reversible archival stays PM-curated.** usa-wa **does** drive `active=false` on biennium-absence, but only under the narrowed condition in Lossy ← item 8: an explicit `GetCommittees(biennium)` membership diff (never current-only `GetActiveCommittees`), guarded by a completeness check + a cohort-floor, emitted one-shot via the producer `active` field (power-map#240) — not routine `to_observation`. PM remains authority for the `active` axis and mirrors it back read-side (#43); the CLI does not set the local column. Separately, PM's reversible `archived_at` signal is still purely PM-curated, mirrored onto the local `archived_at` tombstone (#40/#42) so an archived committee drops from live reads (`active=false` does **not** — it is a non-gating domain flag). Biennium *presence* ("was X active in biennium Y?") remains the P1b Assignment layer's job.

## Cross-references

- **Hybrid IA:** [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md) (v1.4 includes the Role / district / jurisdiction-FK refactor that this spec consumes)
- **Jurisdictional IA:** [`docs/specs/2026-05-31-jurisdictional-ia-design.md`](2026-05-31-jurisdictional-ia-design.md)
- **Canonical jurisdiction decoupling:** [`docs/specs/2026-06-09-canonical-jurisdiction-decoupling-design.md`](2026-06-09-canonical-jurisdiction-decoupling-design.md) (LegislativeSession FK shape this spec uses)
- **Sibling transformation specs:**
  - [`docs/specs/2026-05-27-transformation-legiscan.md`](2026-05-27-transformation-legiscan.md) — closest analog; LegiScan ingests WSL downstream
  - [`docs/specs/2026-05-27-transformation-ocd.md`](2026-05-27-transformation-ocd.md) — canonical-side semantic alignment
  - [`docs/specs/2026-05-27-transformation-uscongress.md`](2026-05-27-transformation-uscongress.md) — federal stress-test analog
- **PM sidecar integration:** [`docs/specs/2026-06-02-power-map-sync-sidecar-design.md`](2026-06-02-power-map-sync-sidecar-design.md) (downstream of the rows WSL produces)
- **Issues:** [usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3) (P0.5 epic — broader); [usa-wa#14](https://github.com/CannObserv/usa-wa/issues/14) (identity-sync verification, partially unblocked by this spec's P1a)
