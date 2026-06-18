# Transformation: WSL SOAP web services → usa-wa hybrid legislative IA

- **Date:** 2026-06-18
- **Status:** draft (transformation #4 of 4 in the cohort; feeds adapter implementation P1a/b/c)
- **Direction:** **WSL SOAP → ours, only.** WSL is the producer; we never publish back. (Power Map is the archival store for the Person / Organization / Role / Assignment cluster; the sidecar handles that direction, not WSL.)
- **Scope:** Field-level mapping between [`canonical.*` entities](2026-05-27-hybrid-legislative-ia.md) and the SOAP/XML envelopes exposed by `https://wslwebservices.leg.wa.gov/` — Washington State Legislature's nine ASMX web services (`AmendmentService`, `CommitteeActionService`, `CommitteeMeetingService`, `CommitteeService`, `LegislationService`, `LegislativeDocumentService`, `RcwCiteAffectedService`, `SessionLawService`, `SponsorService`). WA Legislature, biennium 2025–26 forward (historical backfills out of scope).
- **Inputs:** hybrid IA v1.4 (this repo, [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)); jurisdictional IA design ([`docs/specs/2026-05-31-jurisdictional-ia-design.md`](2026-05-31-jurisdictional-ia-design.md)); sibling transformation specs (LegiScan / OCD / uscongress) as templates.
- **Outputs:** per-entity correspondence tables; vocabulary mappings; lossy-direction inventory; vocab/schema deltas required to receive WSL data cleanly.
- **Non-goals:** legislative-document text content (P3); Code Reviser identifier parsing (existing `document_identifiers` table covers storage); GovInfo XML mimetype handling; pre-2025 historical backfill; consumption of WSL's non-SOAP surfaces (web pages, calendars, agency reports).

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
| `CommitteeMeetingService` | `GetCommitteeMeetings`, `GetCommitteeMeetingItems`, `GetRevisedCommitteeMeetings` | `BillEvent` (hearing), `BillAction` (executive session items) |
| `LegislativeDocumentService` | `GetDocuments`, `GetDocumentsByClass`, `GetAllDocumentsByClass`, `GetDocumentClasses` | `BillVersion`, `BillSupplement` (analysis, report, fiscal note, summary), `document_identifiers` |
| `SessionLawService` | `GetBillByChapterNumber`, `GetChapterNumbersByYear`, `GetSessionLawByBill`, `GetSessionLawByBillId`, `GetSessionLawByInitiativeNumber` | `Bill.enacted_as`, `bill_statute_changes` |
| `RcwCiteAffectedService` | `GetRcwCitesAffected` | `bill_statutory_citations` |

**P1a (first cut) services in scope:** `CommitteeService` (one op: `GetActiveCommittees`). All others are sketched in this spec for future cuts; their field mappings are placeholders until the implementation lands.

## Schema-level orientation

**Biennium as the scope unit.** Every WSL operation that returns more than one row takes a biennium string. The biennium is the closest WSL has to a "session container" — within a biennium, WA holds annual Regular Sessions (mid-Jan to late-April or sine-die) plus zero or more Special Sessions called by the Governor. WSL does not encode the Regular-vs-Special distinction at the SOAP level; we derive it from calendar conventions and synthesize the `LegislativeSession` rows.

**SOAP envelope shape.** Most operations return a typed list element under the SOAP body — e.g., `GetActiveCommittees` returns an `ArrayOfCommittee` containing zero or more `Committee` elements with fields like `Id`, `Name`, `LongName`, `Acronym`, `Agency` (`House` / `Senate` / `Joint`), `PhoneNumber`. SOAP fault envelopes (`<soap:Fault>`) appear for transport-level failures (bad biennium parameter, service down). Per-operation shapes vary; the adapter's normalize layer handles each one specifically. Exact field names are pinned in the P1a cassette pass (see § Open questions).

**Identifier conventions:**
- **Committee:** `Acronym` (e.g., `HC` for House Capital Budget, `WAYS` for Senate Ways & Means) is the WSL-stable committee identifier. Used as `Organization.source_id` for committee rows.
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
| Live SOAP via `CommitteeService.GetActiveCommittees("2025-26")` | ~50 | `canonical.organizations` (committees, parent = House / Senate / legislature for Joint; `acronym` + `phone` columns populated) |

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

4. **`canonical.organizations.acronym` text nullable** — new column (varchar(64)) holding the canonical short acronym (e.g., `"CB"` for Capital Budget, `"WAYS"` for Senate Ways & Means). Single denormalized column rather than reusing `canonical.organization_identifiers` because: (a) the v1.4 IA UQ `(jurisdiction_id, scheme, value)` would collide when the same acronym appears in different bienniums (different Org rows by `Id`); (b) acronym is semantically a label/display attribute, not a third-party identifier; (c) WSL exposes one acronym per Committee element. The sidecar's `to_observation` emits `org_acronyms: [row.acronym]` (a single-element list) to satisfy PM's [`OrganizationObservationRequest.org_acronyms: list[str]`](../../packages/powermap-client/powermap_client/models/organization_observation_request.py) shape; PM's match cascade folds cross-biennium rows by shared acronym at the PM layer, not in our local canonical model.

5. **`canonical.organizations.phone` text nullable** — new column (varchar(64)) holding a primary phone number (e.g., committee staff phone from `Committee.PhoneNumber`). Single denormalized column rather than a 1:N `organization_contact_methods` table because: (a) WSL exposes one phone per committee; (b) the sidecar's `to_observation` wraps it as `contact_methods: [{contact_type: "phone", value: row.phone}]` per PM's [`ObservationContactMethod`](../../packages/powermap-client/powermap_client/models/observation_contact_method.py) shape; (c) future multi-method support (people with multiple emails, secondary phones) can land alongside person ingestion in P1b.

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
  <Id>27</Id>
  <Name>Capital Budget</Name>
  <LongName>House Committee on Capital Budget</LongName>
  <Agency>House</Agency>
  <Acronym>CB</Acronym>
  <PhoneNumber>(360) 786-7100</PhoneNumber>
</Committee>
```

| usa-wa column | WSL field | Direction | Transform | Notes |
|---|---|---|---|---|
| `source_id` | `Id` | ← | passthrough (string) | WSL committee identifier. Biennium-scoped: WSL re-keys `Id` across bienniums, so a committee's canonical Org row is per-(committee, biennium) tuple. Cross-biennium continuity is established at the PM layer through shared `acronym` (see § Vocabulary additions item 4). |
| `name` | `LongName` | ← | direct | E.g., `"House Committee on Capital Budget"`. |
| `short_name` | `Name` | ← | direct | E.g., `"Capital Budget"`. |
| `acronym` | `Acronym` | ← | passthrough (uppercase) | New column (see § Vocabulary additions item 4). E.g., `"CB"`, `"WAYS"`. Sidecar emits as `org_acronyms: [acronym]` in `to_observation`. |
| `org_type` | (always `committee`) | → adapter | constant | Subcommittees use `subcommittee` (P1b — `GetCommittees` returns subcommittees too; deferred). |
| `parent_organization_id` | `Agency` | ← | `"House"` → House Org id; `"Senate"` → Senate Org id; `"Joint"` → legislature Org id | The synthesized anchor IDs are passed into the adapter via a `BootstrapAnchors` dataclass so normalize can resolve `Agency` text → parent FK. Joint committees parent at the legislature level (cleaner than maintaining a synthesized `Joint` chamber row). |
| `jurisdiction_id` | (always `usa-wa`) | → adapter | constant | Same FK as the anchors. |
| `phone` | `PhoneNumber` | ← | direct (strip whitespace) | New column (see § Vocabulary additions item 5). Sidecar wraps as `contact_methods: [{contact_type: "phone", value: phone}]` in `to_observation`. |
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

### `canonical.persons` (P1b — sketched)

`SponsorService.GetSponsors(biennium)` and `LegislationService.GetSponsors(bill_id, biennium)` return WSL sponsor records. Detailed mapping deferred to the P1b implementation; sketch:

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

### `canonical.assignments` (P1b — sketched)

Chamber + party + committee memberships. WSL surfaces these in three places:

- `SponsorService.GetSponsors(biennium)` returns chamber + party for current members.
- `CommitteeService.GetActiveCommitteeMembers(committee_acronym, biennium)` returns committee membership.
- `CommitteeService.GetCommitteeMembers(committee_acronym, biennium)` returns historical membership.

Detailed mapping deferred to P1b. The pattern matches the LegiScan spec (chamber Assignment = `(Person, chamber_role, valid_from=biennium_start)`; committee Assignment = `(Person, committee_role, …)` with party derived as a separate Assignment to a synthesized Party Org).

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

4. **Subcommittee parent inference.** `GetActiveCommittees` does not return subcommittees (only top-level committees per chamber + Joint). `GetCommittees` does include subcommittees but the parent inference relies on naming conventions (`House Capital Budget Subcommittee on …`). P1b will need a small lookup table or regex pattern to identify subcommittees and their parent committees.

5. **Joint committee chamber attribution.** WSL classifies Joint committees as `Agency="Joint"`; they have no chamber parent. We park them under the legislature Org. Reconstructing "which chamber's staff drives this Joint committee" requires external knowledge.

6. **WSL `Id` per biennium re-keying.** `Id` is the per-biennium committee identifier WSL guarantees stable within that biennium. Across bienniums, WSL may re-key the `Id` (committees can be renamed/restructured to reflect scope changes, and `Id` may or may not be stable through such changes — TBD empirically). Our `source_id=Id` choice gives biennium-scoped Organization rows; cross-biennium continuity is established at the PM layer through the shared `acronym` column (see § Vocabulary additions item 4) — when an acronym is stable across bienniums (the common case for WA committees, e.g., `WAYS`, `CB`), PM's match cascade folds rows; when not (committee splits/merges), they remain distinct, which is the correct semantic.

### Lossy → (us → WSL)

**N/A — we never publish back.** WSL is consume-only.

## Open questions

1. **Cross-biennium `Acronym` stability.** Committees are guaranteed stable within a biennium. Across bienniums, WSL may rename committees to indicate changed purpose/scope (e.g., a "Capital Budget" committee re-scoped as "Capital Budget & Housing"); whether `Id` and/or `Acronym` change at the same time is unclear empirically. *Resolution:* the schema accommodates both axes — per-biennium Organization rows (via `source_id=Id`) and cross-biennium continuity through the `acronym` column at the PM-matching layer (PM folds rows by shared acronym when stable; keeps them distinct when not). P1a cassette inspection records the 2025-26 state; subsequent biennium captures (2027-28 in early 2027) test the stability hypothesis. No spec change required either way — both stable and unstable acronyms are correctly represented.

   The same cassette pass pins down exact `Committee` field names (this spec asserts `Agency` / `Acronym` / `LongName` / `Name` / `PhoneNumber` based on WSL documentation patterns, but observed XML may show different casing — e.g., `AgencyName` vs. `Agency`). The first P1a step records cassettes against live WSL and the normalize layer codes against the recorded shape.

2. **Subcommittee detection rule.** `GetActiveCommittees` does not return subcommittees; `GetCommittees` does (P1b scope). *Resolution:* the subcommittee result set is small enough to audit by hand — when P1b lands, run the operation once against live WSL, inspect the output for programmatic patterns (likely a `ParentCommitteeAcronym` field or naming convention), and code the parent inference accordingly. Fall back to a hand-curated `(subcommittee_acronym → parent_acronym)` mapping table maintained alongside the adapter package if no programmatic rule is reliable. *(Defer to P1b — concrete approach decided after live inspection.)*

3. **Special session synthesis trigger.** Specials are unscheduled and irregular. The first cut covers Regular sessions only. *Resolution:* operator supplies known past Special session metadata (start date, end date, biennium) as configuration; the adapter inspects WSL SOAP responses (which scope by biennium, not session) for entities introduced/acted during that window and tags them to the correct Special session row. Going forward, the same operator-supplied-config pattern handles new Specials as the Governor proclaims them. *(Defer to P1b — operational, not blocking.)*

4. ✅ **`Committee.PhoneNumber` and other contact data.** Resolved — phone is in scope for P1a via the new `canonical.organizations.phone` column (see § Vocabulary additions item 4). Sidecar's `to_observation` extension to emit `contact_methods` is a separate follow-up issue but does not block the adapter implementation.

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
