# Hybrid legislative information architecture

- **Date:** 2026-05-27
- **Status:** final (v1 — synthesizes findings from three transformation specs)
- **Scope:** All canonical legislative-domain entities. Supersedes the P0-skeleton entity descriptions in [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md) §Canonical data spine.
- **Tracks:** [GH #3](https://github.com/CannObserv/usa-wa/issues/3); see plan at [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md).
- **Supersedes:** the v0 draft of this file (commit `aed896c`); see Changelog § below.

## Changelog (v1.3 → v1.4, 2026-06-02 — Jurisdictional IA refactor)

The Jurisdictional IA design ([`2026-05-31-jurisdictional-ia-design.md`](2026-05-31-jurisdictional-ia-design.md)) replaces the prior text-shaped `jurisdiction_id` partition tag with an authoritative FK to a Power Map-mirrored cache. Driven by [`docs/plans/2026-05-31-jurisdictional-ia-implementation.md`](../plans/2026-05-31-jurisdictional-ia-implementation.md) and tracked at [usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3).

| Revision | Source | Section |
|---|---|---|
| **Schema-wide `jurisdiction_id: text(32)` → `ULID NOT NULL FK`** across 34 canonical tables (every `Person*` / `Organization*` / `Role` / `Assignment` / `Bill*` / `Vote*` / `LegislativeSession` / `Statute*` / `Lobbying*` / `Contribution` / `DocumentIdentifier` row carries this column; the `*` wildcards include the per-entity identifier sibling tables like `person_identifiers` / `organization_identifiers`). FK target: `clearinghouse_core.jurisdictions.id`. The natural-key UNIQUE constraints (e.g., `(jurisdiction_id, source, source_id)`) survive the column-type change — uniqueness shifts from text to FK comparison. | Jurisdictional IA design §2 2026-06-02 | Schema-wide |
| **`Role.district: text(32)` dropped.** The district reference now lives on `Role.jurisdiction_id` directly — the FK points at a specific district jurisdiction (`usa-wa-ld-21` for Senator-LD21; `usa-wa` for Speaker of the House). `uq_roles_org_name_district` collapses to `uq_roles_org_name = (jurisdiction_id, organization_id, name)` because the district context now rides on `jurisdiction_id`. | Jurisdictional IA design §2 2026-06-02 | Identity cluster |
| **`clearinghouse_core.jurisdictions` extended in place.** New columns: `pm_jurisdiction_id` (anchor for sidecar sync), `type_id` FK → `jurisdiction_types`, bitemporal `valid_from` / `valid_until` / `recorded_at` / `superseded_at` (mirrors PM's clock). Dropped: the 4-value `JurisdictionLevel` StrEnum. Widened: `slug` to text(128); `name` to text. | Jurisdictional IA design §1 2026-06-02 | Framework primitive |
| **Three new framework tables.** `clearinghouse_core.jurisdiction_types` (16-row lookup mirroring PM's 16 type values); `clearinghouse_core.jurisdiction_relationship_types` (11-row lookup with `is_symmetric` + `category` columns; `exercises_concurrent_jurisdiction` excluded per PM Phase 1); `clearinghouse_core.jurisdiction_relationships` (bitemporal junction over `(subject, object, relationship_type)`; partial unique index closes PG's NULL-distinct UNIQUE gap on `valid_from`). | Jurisdictional IA design §1+§2 2026-06-02 | Framework primitive |
| **101-row WA-relevant pre-seed** of `clearinghouse_core.jurisdictions` from `packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/data/initial_jurisdictions.json`: 1 country + 1 state + 49 LDs + 10 CDs + 39 counties + Seattle. 101 relationships (state→country containment, every sub-state→state containment, Seattle→King County as a worked city-in-county example). | Jurisdictional IA design §2 2026-06-02 | Framework primitive |

## Changelog (v1.2 → v1.3, 2026-05-29/30 — OCD review #2 follow-up decisions)

User decisions on the five "Items still open" items from the OCD transformation spec:

| Revision | Source | Section |
|---|---|---|
| **`VoteEvent.originating_bill_action_id`** FK added — links a vote to the specific `BillAction` it produced or resulted from. Matches OCD's `VoteEvent.bill_action`. Item #16 from OCD review #2 — explicit traceability requirement. | OCD review #2 follow-up 2026-05-29 | Vote cluster |
| `BillAction` decomposition rule documented: multi-target referral actions ("Referred to Health and Ways and Means") are decomposed into **multiple independent `BillAction` rows**, one per target, rather than carrying a related-entity 1:N child table. Resolves item #9 without a schema change. | OCD review #2 follow-up 2026-05-29 | Bill cluster (adapter behavior) |
| **`clearinghouse_core.document_identifiers`** polymorphic table added — captures the rich, parseable identifiers that WA bill texts and amendments carry below the Bill level (Code Reviser bill-text IDs like `H-0043.1`, Code Reviser amendment IDs like `S-5276.3/26`, committee amendment forms like `1066 AMH CPB CLOD 295`, lifecycle-tagged IDs like `EHB 1941.PL`). Polymorphic on `entity_type ∈ {bill_version, amendment}` so striker / substitute amendments that produce new BillVersions don't double-store identifiers. JSONB `parsed_components` column reserved for P1b decomposition. Resolves OCD item #6 without a `BillIdentifier`-on-Bill column. | OCD review #2 follow-up 2026-05-30 | new framework primitive |
| **`canonical.bill_supplements`** added — per-bill-version supplementary documents authored by non-partisan committee staff or regulatory agencies. Four kinds: `bill_analysis` (pre-hearing committee summary), `bill_report` (post-hearing committee summary), `fiscal_note` (agency fiscal impact with `partial`/`final` status + `revision_sequence`), `bill_summary` (chamber or agency brief). Plus `author_organization_id`, P1b enrichment columns (`text`, `structured_data` JSONB), and Archiver sidecar columns (`archival_url`, `archived_at`). Lifecycle integration via `BillAction.supplement_id` FK + `primary_classification='supplement_published'`. Resolves LegiScan transformation §9. | LegiScan review #1 follow-up 2026-05-30 | Bill cluster |
| **`Bill.originating_chamber` and `Bill.current_chamber` text columns → FK columns** to `canonical.organizations`. Chambers are first-class Organizations (org_type='chamber'); chamber refs are FKs throughout. The legacy slug form (`"house"` / `"senate"` / `"unicameral"`) is recoverable via `organizations.short_name`. `BillAction.chamber` and `VoteEvent.chamber` stay as text denorms (their parent entities already carry FK refs via `acting_organization_id` / `context_organization_id`). | LegiScan review #2 OR 1 follow-up 2026-05-31 | Bill cluster |
| **WA committee-report nay variants** added to `VoteCount.count_type` and `PersonVote.vote` vocab: `nay_without_rec` (without recommendation, lighter rejection) and `nay_do_not_pass` (DNP, stronger rejection). Plain `nay` remains the floor-vote default. Application-layer enforcement only (text columns; no DB CHECK so other jurisdictions stay flexible). | LegiScan review #2 vote-vocab follow-up 2026-05-31 | Vote cluster |
| **`Bill.classification: text(32) nullable`** added — OCD-aligned semantic classification orthogonal to `bill_type`'s prefix-encoded form (`bill` / `resolution` / `joint resolution` / `concurrent resolution` / `simple resolution` / `memorial` / `proclamation` / `initiative` / etc.). Adapter derives from `bill_type` or source signal. Enables "all resolutions this session" / "all initiatives" queries without parsing `bill_type` strings. | uscongress review #1 OQ14 follow-up 2026-06-01 | Bill cluster |
| **`BillSponsorship.sponsored_at: timestamptz nullable`** added — when the sponsor signed on. Federal `cosponsors[].sponsored_at` populates this; recovers original-cosponsor inference by comparing to `Bill.introduced_at`. WA uses this for cosponsors who join after introduction. | uscongress review #1 OQ8 follow-up 2026-06-01 | Bill cluster |
| **`nomination`** added to `VoteEvent.subject_type` vocab. WA Senate confirmation of gubernatorial appointments (and the federal-Senate analog of presidential nominations / treaty ratifications). When `subject_type='nomination'`, `subject_id` points to the nominee Person until a dedicated Nomination / Appointment entity lands in P1b. Adapter writes appointment text to `motion_description`. | uscongress review #1 OQ10 follow-up 2026-06-01 | Vote cluster |
| **Document identifier UNIQUE relaxation.** `(jurisdiction_id, scheme, value)` UNIQUE → `(jurisdiction_id, entity_type, scheme, value)`. Required for the WA case where a substitute/striking amendment AND the resulting BillVersion legitimately share the same Code Reviser ID (e.g., `H-0734.1/25`) — same underlying document, two manifestations. The new constraint still prevents accidental collision within an entity_type. | Hybrid IA review #1 item 1 2026-06-02 | Framework primitive |
| **`LegislativeSession.adjourned_sine_die_at`** dropped — functionally redundant with `end_date` for the WA use case. `end_date` now carries sine die semantics for adjourned sessions; documented in the column comment. | Hybrid IA review #1 item 3 2026-06-02 | Session cluster |
| **`canonical.bill_types` lookup table.** `Bill.bill_type` + `Bill.classification` text columns → single `Bill.bill_type_id` FK. The lookup carries code (`HB`/`SB`/`HJM`/etc.), `display_name`, and the OCD-aligned `classification` (`bill`/`resolution`/`memorial`/etc.). Stops drift — `bill_type` and `classification` were independently editable per-row; now they ride together on the lookup. | Hybrid IA review #1 item 5 2026-06-02 | Bill cluster |
| **`BillAction.chamber`** dropped — derivable from `acting_organization_id` (the chamber Org for chamber-level actions; ancestor of `acting_organization_id` when a committee acts). For executive actions, `acting_organization_id` points at the executive Org (e.g., "WA Office of the Governor"). | Hybrid IA review #1 item 6 2026-06-02 | Bill cluster |
| **Amendment → BillVersion relinkage.** `Amendment.bill_id` → `Amendment.bill_version_id` (every amendment is against a specific version). Substitute / striking amendments point at the newly proposed BillVersion (which exists as a row from the moment the substitute is offered). `Amendment.amendment_text` dropped (substitute / striking amendments use their target BillVersion's text). `BillVersion.amendment_id` back-link dropped — relinking is forward-only now. | Hybrid IA review #1 item 8 2026-06-02 | Bill cluster |
| **`bill_titles.chamber` text → Org FK.** `chamber_id` joins `canonical.organizations`. Same chamber-as-Org-FK principle applied across the schema. | Hybrid IA review #1 item 9 2026-06-02 | Bill cluster |
| **`canonical.bill_relationship_types` lookup table** with `symmetric` boolean column. `bill_relationships.relationship_type` text → `relationship_type_id` FK. The lookup encodes the `symmetric` property (companion is symmetric; replaces / replaced_by are directed) once per type rather than relying on per-relationship documentation. | Hybrid IA review #1 item 10 2026-06-02 | Bill cluster |

Items closed after v1.3: `BillSponsorship.role` (item #12) stays at the 4-value enum (closed with rationale); `BillVersion.text` canonicalization rules stay as the open design discussion in §"Open issues".

## Changelog (v1.1 → v1.2, 2026-05-28 — continued OCD review)

A second pass of the OCD spec review surfaced eight more concrete revisions:

| Revision | Source | Section |
|---|---|---|
| `LegislativeSession.classification` vocab tightened to `regular` / `special` / `other` (dropped `extraordinary` — no semantic difference from `special` — and `sine_die`, which is an adjournment state, not a session type). New column `adjourned_sine_die_at: timestamptz nullable` captures WA's sine-die-adjournment timestamp. | OCD review #2 2026-05-28 | LegislativeSession |
| **`Bill.short_description` moved to `BillVersion.short_description`.** OCD `BillAbstract` is per-version, not per-bill — collapsing to a single column on Bill loses resolution. | OCD review #2 2026-05-28 | Bill + BillVersion |
| **`Bill.current_text` removed; replaced by `Bill.current_version_id` FK to BillVersion.** The "current text" is now `bill.current_version.text`. Single source of truth for bill text lives on BillVersion. | OCD review #2 2026-05-28 | Bill + BillVersion |
| **`BillVersion.text`** added — canonical plain-text representation of the bill at this version. Storage/canonicalization rules (MIME handling, OCR for image-PDFs, styled-vs-plain) are an open design question; see Open issues. | OCD review #2 2026-05-28 | BillVersion |
| **`BillVersion.amendment_id`** FK added — when an adopted striking/substitute amendment produced this version, FK points back. Null for introduced and engrossed-by-action versions. | OCD review #2 2026-05-28 | BillVersion |
| **`Amendment.amendment_kind`** added — `traditional` / `striking` / `substitute`. WA practice treats strikers and substitutes as full bill replacements; when adopted, they produce new BillVersion rows. | OCD review #2 2026-05-28 | Amendment |
| New table `canonical.bill_version_links` (1:N) — alternative representations of a BillVersion: HTML / PDF / image-PDF / processed-text / redline. | OCD review #2 2026-05-28 | new Bill cluster entity |
| New table `canonical.bill_statutory_citations` — statutory references extracted from a BillVersion's text, FK to `canonical.statute_sections` when resolved. | OCD review #2 2026-05-28 | new cross-cluster entity |
| New table `clearinghouse_core.notes` — polymorphic editorial / staff-summary / clarification / provenance notes attached to any canonical entity. Replaces a hypothetical `BillVersion.note` column; reusable across the domain. | OCD review #2 2026-05-28 | new framework primitive |

## Changelog (v1 → v1.1, 2026-05-28)

OCD transformation review surfaced three additional revisions:

| Revision | Source | Section |
|---|---|---|
| `canonical.bill_titles` 1:N child table — bills carry multiple titles (canonical / short / popular / official / display / alternative / long), each with chamber, as-of-action, language, and an optional `amendment_id` for WA's amendment-driven title-change tracking. `Bill.title` is now a denormalized "current canonical title" sync'd from this table. | OCD review feedback 2026-05-28 | Bill cluster |
| `canonical.persons.birth_year` **removed**. Birth date + birth place + death date + other lifecycle events defer to Power Map's polymorphic `lifecycle_events` schema (planned [power-map#165](https://github.com/CannObserv/power-map/issues/165)). usa-wa caches identity essentials only. | OCD review feedback 2026-05-28 | Identity cluster |
| New "Rich attributes deferred to Power Map" section documents the broader principle: image / email / biography / office / links / sources / phone for Person and Organization live in Power Map's polymorphic primitives (locations, contact_methods, links, note). usa-wa's local schema doesn't duplicate. | OCD review feedback 2026-05-28 | new top-level section |

**Transformation specs are unidirectional, not bidirectional.** OCD / LegiScan / uscongress transformation specs map their data into our shape; we never push data back to those systems. The transformation specs' direction columns are corrected accordingly in the same review pass.

## Changelog (v0 → v1)

v0 was pressure-tested against three foreign schemas by parallel transformation agents. Their outputs:

- [`docs/specs/2026-05-27-transformation-ocd.md`](2026-05-27-transformation-ocd.md) — Open Civic Data / OpenStates
- [`docs/specs/2026-05-27-transformation-legiscan.md`](2026-05-27-transformation-legiscan.md) — LegiScan API
- [`docs/specs/2026-05-27-transformation-uscongress.md`](2026-05-27-transformation-uscongress.md) — federal `unitedstates/congress` + `congress-legislators`

Concrete revisions landed in v1:

| Revision | Source(s) | Section |
|---|---|---|
| `canonical.person_identifiers` + `canonical.organization_identifiers` 1:N child tables for external IDs (bioguide, LIS, FollowTheMoney, Votesmart, OpenSecrets, Ballotpedia, etc.). Existing `powermap_*_id` columns retained as denormalized fast-path. | OCD #3, LegiScan #1, uscongress #1 | External identifiers |
| `canonical.bill_relationships` table for companion / replaces / replaced-by / related-to / prior-session-carryover. | OCD #4, LegiScan honorable | Bill cluster |
| `canonical.bill_subjects` child table (subjects-per-bill from source vocab). | OCD honorable, LegiScan honorable | Bill cluster |
| `canonical.bill_events` (replaces the P0-skeleton `Hearing`) for hearings, work sessions, executive sessions. | LegiScan honorable | Bill cluster |
| `canonical.bill_action_classifications` 1:N child table; `BillAction.primary_classification` denormalized for display. | OCD #1 | Bill cluster |
| `BillAction.display_order` + `BillAction.is_major`. | OCD honorable, LegiScan #3 | Bill cluster |
| `Bill.originating_chamber` + `Bill.current_chamber` (replaces single `chamber`). | LegiScan #2 | Bill cluster |
| `Bill.current_status_class` (normalized) + `Bill.current_status_at` (timestamp); `Bill.current_step` removed. | LegiScan #4, uscongress #4 | Bill cluster |
| `Bill.enacted_as` (Public Law / chapter-law cross-reference). | uscongress #3 | Bill cluster |
| `VoteEvent.category` (procedural/substantive/passage/cloture/recommit/nomination/treaty/conviction/other). | uscongress #2 | Vote cluster |
| `BillSponsorship.sponsor_name_raw`, `PersonVote.voter_name_raw`, `Assignment.holder_name_raw` — fallback columns for indirect-provider adapters where ID resolution hasn't happened yet. | OCD #2 | Identity cluster + Bill cluster + Vote cluster |
| `Role.district` (text nullable). Moves district context off `Person.current_district`. *(Dropped in v1.4 — the schema-wide jurisdiction_id FK refactor collapses district context onto `Role.jurisdiction_id` directly; see the v1.4 changelog at the top.)* | OCD #5 | Identity cluster |

**Documented as unavoidable lossy** (see Unavoidable lossy directions §):

- Amendments → OCD: OCD has no `Amendment` entity; emit as `BillAction.classification ∈ amendment-*`.
- Bill classification array → our scalar: OCD bills can be multi-classified (resolution + concurrent + appropriation); we stay scalar.
- LegiScan VoteEvent narrowness: no committee, amendment, or motion votes.
- LegiScan Amendment lifecycle collapse: no sponsor IDs, single `adopted` flag.
- congress floor-only votes: federal upstream doesn't collect committee votes.
- congress committee membership historical gap: upstream YAML is current-state-only.

## Problem

The P0 entity skeleton in `clearinghouse-domain-legislative` was sized for "one WA bill end-to-end" and did not survive contact with the multi-state IA delta ([`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`](../research/2026-05-26-multi-state-legislative-ia-delta.md)). Three structural gaps and one naming inversion needed addressing before any P1a normalization code lands:

1. `Bill.biennium` as a text column leaks across special sessions.
2. No vote cluster at all — but votes are universal in OpenStates / LegiScan / uscongress, and WSL SOAP returns roll calls.
3. `BillSponsorship` is two-valued (prime/co) and assumes a non-null `legislator_id`, which can't represent committee-sponsored bills (federal Congress, some states).
4. `Bill.title` / `Bill.short_description` are inverted relative to industry convention.

A larger restructuring was decided during P0.5 planning: adopt power-map's identity vocabulary (Person / Organization / Role / Assignment) for all of `Legislator`, `Committee`, and `Filer`. This aligns usa-wa's identity model with the cohort's canonical-identity service and positions usa-wa as a producer of identity data archived in power-map. See the producer/archival framing below and [project_identity_producer_archival](../../../.claude/projects/-home-exedev-usa-wa/memory/project_identity_producer_archival.md).

## Producer/archival framing

usa-wa is **both** a query layer over primary WA sources **and** a producer of identity data. As the WA Legislature adapter ingests SOAP responses, it produces canonical Person / Organization / Role / Assignment records in usa-wa's local Postgres. The long-term direction is to push those records to [power-map](https://github.com/CannObserv/power-map) as the cohort's identity system of record. usa-wa keeps a local copy for query latency; the canonical truth lives upstream once the power-map write API ([CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156) §9) is available.

Consequences:

- **The canonical identity model uses power-map's vocabulary** so cross-service joins work without translation: Person, Organization, Role, Assignment.
- **Each Person and Organization carries a nullable `powermap_*_id` column** that's populated when power-map confirms a mapping. Until then, usa-wa's own ULID is the local truth.
- **The new `*_identifiers` child tables (v1)** hold the full N-scheme external-ID graph that OCD, LegiScan, uscongress all maintain (bioguide, LIS, FollowTheMoney, Votesmart, OpenSecrets, Ballotpedia, KnowWho, etc.). `powermap_*_id` stays as the primary cross-cohort denormalization; the child table absorbs the rest.
- **A future archival-push job** stages local-canonical → power-map writes when the upstream write API matures (P3+). The records' shape is designed today to make that push mechanical (no schema impedance mismatch).
- **State-resource resilience.** When WSL SOAP rate-limits, breaks compatibility, or rotates IDs, the local cache and the archival truth in power-map keep MCP/REST queries serving.

## Universal entity shape

Every canonical entity in this spec carries the following columns. Per-entity blocks below only call out domain-specific additions.

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | Always. Auto-generated via `clearinghouse_core.db.ulid.ULID`. |
| `jurisdiction_id` | text(32) NOT NULL, indexed | Slug per `feedback_jurisdiction_naming` — `usa-wa`, `usa-or`, `usa-fed`. |
| `source` | text(64) NOT NULL | Matches the producing adapter's `source_slug`. |
| `source_id` | text(128) NOT NULL | Source-stable identifier within the adapter. |
| `primary_source_id` | ULID nullable | Denormalized FK to `clearinghouse_core.sources.id`. |
| `last_fetched_at` | timestamptz nullable | Last successful normalization fetch. |
| `last_fetch_event_id` | ULID nullable | FK to `clearinghouse_core.fetch_events.id`. |
| `created_at` | timestamptz NOT NULL, server_default=now() | Via `TimestampMixin`. |
| `updated_at` | timestamptz NOT NULL, server_default=now(), onupdate=now() | Via `TimestampMixin`. |

**Natural-key UNIQUE constraint:** `UNIQUE (jurisdiction_id, source, source_id)` on every entity unless a per-entity block specifies otherwise.

All FKs use the `ULID` SQLAlchemy column type. Schema is `canonical.*` for every table in this spec.

## Notes (polymorphic, new in v1.2)

`clearinghouse_core.notes` is a polymorphic editorial / staff-summary / clarification / provenance note attached to any canonical entity. Pattern mirrors `Citation`:

| Column | Type | Notes |
|---|---|---|
| `entity_type` | text(64) NOT NULL | `bill` / `bill_version` / `amendment` / `person` / `organization` / etc. |
| `entity_id` | ULID NOT NULL | Polymorphic; no DB FK. |
| `note_kind` | text(32) NOT NULL | `staff_summary` / `editorial` / `clarification` / `provenance` / `other` (free text but documented vocab). |
| `text` | text NOT NULL | |
| `author_person_id` | ULID nullable | Polymorphic; references `canonical.persons.id` when set. |
| `author_organization_id` | ULID nullable | Polymorphic; references `canonical.organizations.id` when set. |
| `effective_at` | timestamptz nullable | When the note was written / effective. |

The original motivating case (from the OCD review): WA amendments come with **official, non-partisan staff-prepared effects descriptions**. These attach to the `Amendment` entity as `Note(entity_type='amendment', entity_id=<amendment_id>, note_kind='staff_summary', author_organization_id=<senate_or_house_committee_services_org_id>)`. Same pattern generalizes to bill editorial notes, person biographical clarifications, organization provenance notes, etc. Rather than adding a per-entity `note` column to every domain table, one polymorphic table handles all of them.

## Document identifiers (polymorphic, new in v1.3)

`clearinghouse_core.document_identifiers` captures the rich, parseable identifiers WA bill texts and amendments carry below the Bill level. The overall `Bill` keeps a stable identifier (e.g., `HB 1941`); the *texts* and *amendments* below it carry their own scheme-tagged identifiers from issuing authorities (Code Reviser, chamber committee staff). Same polymorphic pattern as `Citation` and `Note`.

| Column | Type | Notes |
|---|---|---|
| `jurisdiction_id` | text(32) NOT NULL | Standard. |
| `source`, `source_id` | text NOT NULL | Standard provenance pair. |
| `entity_type` | text(32) NOT NULL | `bill_version` / `amendment` (extensible). |
| `entity_id` | ULID NOT NULL | Polymorphic; no DB FK. |
| `scheme` | text(64) NOT NULL | Jurisdiction-prefixed slug — see WA examples below. |
| `value` | text(256) NOT NULL | The identifier as published by the issuing authority. |
| `parsed_components` | jsonb nullable | Populated lazily by P1b enrichment; structured decomposition of `value`. |
| `verified_at` | timestamptz nullable | When the mapping was last confirmed. |

**Uniqueness:** `(jurisdiction_id, source, source_id)` standard natural key, **plus** `(entity_type, entity_id, scheme)` so a given bill version or amendment carries at most one value per scheme, **plus** `(jurisdiction_id, entity_type, scheme, value)` so the same identifier doesn't accidentally attach to two different entities **of the same kind** within a jurisdiction. **v1.3 (2026-06-02):** the constraint includes `entity_type` so the same identifier (e.g., WA Code Reviser `H-0734.1/25`) can legitimately attach to BOTH an Amendment row AND a BillVersion row when a substitute/striking amendment becomes a new bill text — same document, two manifestations.

**Striker/substitute polymorphism is the load-bearing case.** When an `Amendment(amendment_kind='substitute')` is adopted and produces a new `BillVersion`, *each entity carries its own identifiers* under its own `entity_type`. The Amendment row holds the proposal's identifier (`S-5276.3/26`); the resulting BillVersion holds whatever bill-text identifier the source assigns post-adoption. No duplication; traversal between the two stays on `BillVersion.amendment_id`.

**WA schemes (the v1.3 starting set):**

| Scheme | Applies to | Example | Future decomposition |
|---|---|---|---|
| `usa_wa_code_reviser` | bill_version, amendment | `H-0043.1`, `S-5276.3/26` | `{chamber, sequence, draft, year}` |
| `usa_wa_committee_amendment` | amendment | `1066 AMH CPB CLOD 295`, `1141-S.E AMS LC S2598.1` | `{bill_number, version_prefix, chamber, committee_abbr, drafter_initials, sequence, code_reviser_id}` |
| `usa_wa_lifecycle_tag` | bill_version | `EHB 1941.PL`, `SB 5069.SL` | `{version_prefix, bill_type, bill_number, status_suffix}` |

Other jurisdictions add scheme slugs in their own namespace (`usa_or_lc_*`, `usa_ca_legcounsel_*`, etc.).

## Rich attributes deferred to Power Map

OCD / LegiScan / uscongress all carry per-Person and per-Organization rich attributes that Power Map already models as polymorphic primitives. **usa-wa does not duplicate these locally.** Adapters that ingest them push to Power Map via the sidecar; readers join through `powermap_*_id`.

| Attribute | Source examples | Power Map primitive | Local storage in usa-wa? |
|---|---|---|---|
| Address / office | OCD `PersonOffice` | `locations` (polymorphic — already attached to Persons and Organizations) | **No.** Push to Power Map. |
| Email | OCD `Person.email` | `contact_methods` (kind=`email`) | **No.** Push to Power Map. |
| Phone | (various) | `contact_methods` (kind=`phone`) | **No.** Push to Power Map. |
| Web links | OCD `PersonLink`, `PersonSource`, `Organization.links`, `Organization.sources` | `links` (polymorphic) | **No.** Push to Power Map. |
| Image / headshot | OCD `Person.image` | `links` (kind=`image`) | **No.** Push to Power Map. |
| Biography | OCD `Person.biography` | Power Map `note` field on the entity | **No.** Push to Power Map. |
| Birth date + place | OCD `Person.birth_date` | `lifecycle_events` (planned — [power-map#165](https://github.com/CannObserv/power-map/issues/165)) | **No.** Push to Power Map once #165 ships. |
| Death date + place | OCD `Person.death_date` | `lifecycle_events` (planned — #165) | **No.** |
| Founded / dissolved date (Organization) | (various) | `lifecycle_events` (planned — #165) | **No.** |

**Rule of thumb:** if Power Map has (or will have) a primitive for it, usa-wa's local schema doesn't. The local cache stores identity essentials (Person name, Organization name, the `*_id` discriminators) and the FK to Power Map; everything else flows upstream.

**Until power-map endpoints (#158, #164) ship**, the sidecar stages these pushes locally — the data still gets captured during ingestion, just not written to Power Map yet. Storage shape for the staging queue lands when the sidecar is implemented (P2+).

## Identity cluster (power-map terminology)

### `canonical.persons`

A human. Replaces `Legislator`. Local cache of identity essentials only — rich attributes (image, email, biography, office, links, sources, lifecycle events) live in Power Map (see "Rich attributes deferred to Power Map" below).

| Column | Type | Notes |
|---|---|---|
| `name_full` | text NOT NULL | Most-canonical full name available at ingest time. |
| `name_first` | text nullable | |
| `name_last` | text nullable | |
| `name_middle` | text nullable | |
| `name_suffix` | text nullable | "Jr.", "III", etc. |
| `name_used` | text nullable | Preferred display when different from legal name. |
| `gender` | text(32) nullable | Source's free-text value. |
| `powermap_person_id` | ULID nullable | Set after a power-map match. |

Notes:
- `birth_year` removed (v1.1, post-transformation-review 2026-05-28). Birth date + birth place + death date defer to Power Map's polymorphic `lifecycle_events` schema ([CannObserv/power-map#165](https://github.com/CannObserv/power-map/issues/165)). usa-wa caches identity essentials only; lifecycle events belong upstream.
- `current_district` removed (v0 → v1). District context lives on `Role` now; see `canonical.roles`. *(v1.4: now on `Role.jurisdiction_id` as a FK to the district jurisdiction, not a text label.)*

### `canonical.organizations`

| Column | Type | Notes |
|---|---|---|
| `name` | text NOT NULL | Canonical full name. |
| `short_name` | text nullable | |
| `org_type` | text(32) NOT NULL | One of: `chamber` / `party` / `committee` / `subcommittee` / `caucus` / `candidate_committee` / `lobbying_firm` / `pac` / `government_agency` / `other`. |
| `parent_organization_id` | ULID nullable FK self | A committee's parent is its chamber; a subcommittee's parent is its committee. |
| `powermap_organization_id` | ULID nullable | |

### `canonical.roles`

A named slot **within** an Organization.

| Column | Type | Notes |
|---|---|---|
| `organization_id` | ULID NOT NULL FK | |
| `name` | text(64) NOT NULL | "Senator", "Representative", "Delegate", "Resident Commissioner", "Chair", "Vice Chair", "Ranking Member", "Member", "Speaker", "President Pro Tempore", etc. |
| `role_type` | text(32) NOT NULL | One of: `elected_member` / `leadership` / `committee_member` / `committee_leadership` / `staff` / `party_member` / `other`. |

**v1.4:** the prior `district: text(32) nullable` column was dropped. District context now rides on the inherited `jurisdiction_id: ULID NOT NULL FK` (every canonical entity carries this) which FKs into `clearinghouse_core.jurisdictions`. A Senator-LD21 Role's `jurisdiction_id` points at the `usa-wa-ld-21` cache row; a Speaker-of-the-House Role's `jurisdiction_id` points at `usa-wa`. The containment graph in `clearinghouse_core.jurisdiction_relationships` recovers "LD 21 is contained by WA" without per-row text.

**Natural-key UNIQUE:** `(jurisdiction_id, organization_id, name)` (v1.4; was `(jurisdiction_id, organization_id, name, district)` pre-v1.4). Roles for the same chamber-position in different districts are distinct because their `jurisdiction_id` FK differs.

Examples — note the federal-stress-test-driven distinction between Representative / Delegate / Resident Commissioner as separate role names. The WA-deployment in-scope examples come first; the federal examples are illustrative for a hypothetical federal deployment and reference slugs (`usa-dc`, `usa-pr`) that are **not currently seeded** in `clearinghouse_core.jurisdictions` (the WA-only `initial_jurisdictions.json` covers WA + WA-relevant containers only).

WA-deployment (currently seeded):

- `(org=WA Senate, jurisdiction=<usa-wa-ld-21>, name="Senator", role_type="elected_member")`
- `(org=US House, jurisdiction=<usa-wa-cd-3>, name="Representative", role_type="elected_member")`
- `(org=Senate Health Committee, jurisdiction=<usa-wa>, name="Chair", role_type="committee_leadership")` — at-large / leadership / committee roles point at the broadest containing jurisdiction the role operates within

Federal stress-test (illustrative; slugs **not** in the WA-only seed):

- `(org=US House, jurisdiction=<usa-dc>, name="Delegate", role_type="elected_member")` — non-voting (jurisdiction = the territory served, modeled as a federal-administrative jurisdiction)
- `(org=US House, jurisdiction=<usa-pr>, name="Resident Commissioner", role_type="elected_member")` — 4-year term

### `canonical.assignments`

Person × Role × Period.

| Column | Type | Notes |
|---|---|---|
| `person_id` | ULID NOT NULL FK | |
| `role_id` | ULID NOT NULL FK | |
| `holder_name_raw` | text(256) nullable | **New in v1.** Source-provided name string captured when ID resolution to a known Person hasn't completed. The adapter sets this and leaves `person_id` null pending the next resolution sweep. |
| `valid_from` | date NOT NULL | |
| `valid_to` | date nullable | Null = currently active. |
| `is_active` | bool NOT NULL default false | Denormalized for query speed. |

**Note:** v1 allows `person_id` to be temporarily null when `holder_name_raw` is populated and resolution is in flight. A periodic resolver job converts `holder_name_raw` → `person_id` and clears the raw column.

**Natural-key UNIQUE:** `(jurisdiction_id, person_id, role_id, valid_from)` when `person_id` is non-null; the partial unique index handles null cleanly.

## External identifiers (new in v1)

The OCD / LegiScan / uscongress investigations confirmed every Person and Organization in their schemas carries 5–15 external identifiers (bioguide, LIS, FollowTheMoney, Votesmart, OpenSecrets, Ballotpedia, KnowWho, ICPSR, Wikipedia, etc.). v0's single `powermap_*_id` column collapses that graph. v1 promotes the N-cardinality to dedicated child tables.

### `canonical.person_identifiers`

| Column | Type | Notes |
|---|---|---|
| `person_id` | ULID NOT NULL FK | |
| `scheme` | text(64) NOT NULL | Identifier scheme slug: `bioguide` / `lis` / `ftm_eid` / `votesmart` / `opensecrets` / `ballotpedia` / `knowwho_pid` / `icpsr` / `wikipedia` / `wsl_member_id` / `pdc_filer_id` / etc. |
| `value` | text(128) NOT NULL | The identifier value in the scheme's natural format. |
| `verified_at` | timestamptz nullable | When the mapping was last confirmed. |

**Natural-key UNIQUE:** `(person_id, scheme)` — one value per scheme per person. **Plus:** `(jurisdiction_id, scheme, value)` — one Person owns a given identifier within a jurisdiction.

`Person.powermap_person_id` remains as a denormalized fast-path for the most-common cross-cohort query (`person_identifiers` would have a row with `scheme="powermap"`).

### `canonical.organization_identifiers`

Same shape as `person_identifiers`, FK to `canonical.organizations`. Common schemes: `wsl_committee_id`, `pdc_filer_id`, `fec_committee_id`, `irs_ein`, `opensecrets_org`, `ftm_org_eid`, `powermap`.

## `canonical.legislative_sessions`

| Column | Type | Notes |
|---|---|---|
| `slug` | text(64) NOT NULL | OpenStates-style: `<jurisdiction_id>-<year>[-<session_suffix>]`. Examples: `usa-wa-2025`, `usa-wa-2025-special-1`, `usa-fed-119`. |
| `name` | text NOT NULL | "2025 Regular Session", "2025 First Special Session". |
| `classification` | text(32) NOT NULL | One of: `regular` / `special` / `other`. **v1.2 (2026-05-28)** dropped `sine_die` (an adjournment state, not a session type) and `extraordinary` (no meaningful distinction from `special`). |
| `start_date` | date nullable | |
| `end_date` | date nullable | The date the session ended — sine die for adjourned sessions, scheduled end otherwise. **v1.3 (2026-06-02)** dropped the separate `adjourned_sine_die_at: timestamptz` column as functionally redundant. If precise sine-die timestamps become load-bearing for a query later, add back. |
| `is_active` | bool NOT NULL default false | |
| `biennium_label` | text(16) nullable | WA-flavored — preserved for round-tripping ("2025-26"). |

**Natural-key UNIQUE:** `(jurisdiction_id, slug)`.

## Bill cluster

### `canonical.bills`

| Column | Type | Notes |
|---|---|---|
| `legislative_session_id` | ULID NOT NULL FK | |
| `originating_chamber_id` | ULID NOT NULL FK | **v1.3 (2026-05-31).** FK to `canonical.organizations.id` for the chamber Org (org_type='chamber') where the bill was first introduced. Replaces the `originating_chamber: text(16)` enum column — chambers are first-class Organizations; chamber refs are FKs throughout. The legacy slug form (`"house"` / `"senate"` / `"unicameral"`) is recoverable via `organizations.short_name`. |
| `current_chamber_id` | ULID nullable FK | **v1.3 (2026-05-31).** FK to the chamber Org currently considering the bill. Null when in conference or fully passed both. Replaces the `current_chamber: text(16)` column. |
| `number` | int NOT NULL | |
| `bill_type_id` | ULID nullable FK | **v1.3 (2026-06-02).** FK to `canonical.bill_types`. Replaces the inline `bill_type: text(32)` + `classification: text(32)` columns; both fields now live on the lookup row, so they stay in lockstep without per-row drift. |
| `title` | text NOT NULL | **Denormalized current canonical title** — synced from `bill_titles` where `is_current=true AND title_type='canonical'`. Most queries read this column without joining. |
| `current_version_id` | ULID nullable FK | **v1.2.** FK to `canonical.bill_versions.id` (with `use_alter=True` for circular-FK safety). The bill's current version; `bill.current_version.text` is the canonical text. Replaces the v1 `current_text` column. |
| `current_status` | text(128) nullable | Source-vocabulary text. |
| `current_status_class` | text(32) nullable | **v1.** Normalized to OCD's `bill_action_classification` plus LegiScan's status vocab — values like `introduced` / `in_committee` / `passed_first_chamber` / `passed_second_chamber` / `vetoed` / `signed` / `enacted` / `failed` / `withdrawn`. |
| `current_status_at` | timestamptz nullable | **v1 (replaces `current_step`).** When `current_status` was last updated. |
| `introduced_at` | timestamptz nullable | |
| `enacted_as` | text(64) nullable | **v1.** Public Law / chapter law cross-reference once enacted (federal: "Public Law 119-12"; WA: "Chapter 47, Laws of 2025"). |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

Notes:
- v0's `current_step` column is dropped. The previous use-cases (status enum + when-was-status-set) are replaced by `current_status_class` + `current_status_at`.
- `Bill.title` is denormalized; the full title history (including amendment-driven changes, alternative titles like short / popular / official / display, chamber-specific titles, and historical replaced titles) lives in `canonical.bill_titles` (added v1.1, post-transformation-review). See below.
- v1.2 removed `Bill.short_description` and `Bill.current_text`. Per-version summary lives on `BillVersion.short_description`; canonical text lives on `BillVersion.text` and is accessed via `Bill.current_version_id`.

### `canonical.bill_types` (new in v1.3, 2026-06-02)

Lookup table for bill-type codes. Replaces the inline `Bill.bill_type: text(32)` + `Bill.classification: text(32)` columns. The two fields rode together conceptually (HB → bill, HJM → memorial) but were independently editable per row before — drift risk. Co-locating them on the lookup row eliminates the risk.

| Column | Type | Notes |
|---|---|---|
| `jurisdiction_id` | text(32) NOT NULL | |
| `code` | text(32) NOT NULL | Source prefix code: WA: `HB` / `SB` / `HJM` / `HCR` / etc.; federal: `hr` / `hjres` / `sconres` / etc. |
| `display_name` | text(128) NOT NULL | "House Bill", "Joint Memorial", "Senate Joint Resolution". |
| `classification` | text(32) nullable | OCD-aligned semantic class: `bill` / `resolution` / `joint resolution` / `concurrent resolution` / `simple resolution` / `constitutional amendment` / `memorial` / `proclamation` / `initiative` / `study request` / `other`. |

**Natural-key UNIQUE:** `(jurisdiction_id, code)`. Adapter seeds the lookup at init or upserts on first encounter.

### `canonical.bill_sponsorships`

Polymorphic: a sponsor is either a Person (legislator) or an Organization (committee).

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `person_id` | ULID nullable FK | Exactly one of person_id / organization_id is non-null. |
| `organization_id` | ULID nullable FK | |
| `sponsor_name_raw` | text(256) nullable | **v1.** Source-provided sponsor name when ID resolution hasn't completed. Adapter populates and a later resolver promotes to `person_id` or `organization_id`. |
| `role` | text(32) NOT NULL | `primary` / `co` / `joint` / `generic` (4-value, OCD-aligned). |
| `sponsor_order` | int nullable | |
| `sponsored_at` | timestamptz nullable | **v1.3 (2026-06-01).** When the sponsor signed on. Recovers "original cosponsor" inference (compare to `Bill.introduced_at`). Federal `cosponsors[].sponsored_at` direct mapping; WA uses for late-joining cosponsors. |
| `withdrawn_at` | timestamptz nullable | |

**CHECK constraint:** at most one of `person_id` / `organization_id` is non-null, with `sponsor_name_raw` non-null when both are null (pending resolution).

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

### `canonical.bill_actions`

Append-only lifecycle log.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `action_at` | timestamptz NOT NULL | |
| `acting_organization_id` | ULID nullable FK | The body that took the action — chamber, committee, or executive Org (e.g., "WA Office of the Governor"). **v1.3 (2026-06-02)** dropped the separate `chamber: text(16)` column — chamber is derivable from `acting_organization_id` (the chamber itself for chamber-level actions; an ancestor for committee actions). |
| `action_type` | text(64) NOT NULL | Source-vocab text. |
| `primary_classification` | text(64) nullable | **v1.** Single most-canonical OCD class for display: `introduction` / `reading-1` / `passage` / `amendment-passage` / `committee-passage` / `executive-signature` / `veto-override-passage` / etc. The full multi-class array lives in `bill_action_classifications`. |
| `description` | text NOT NULL | Free-text description. |
| `display_order` | int nullable | **v1.** Tie-breaker for same-day actions; preserves source's intended sequence. |
| `is_major` | bool NOT NULL default false | **v1.** Source's "milestone" flag (LegiScan `importance`). |
| `supplement_id` | ULID nullable FK | **v1.3 (2026-05-30).** FK to `canonical.bill_supplements.id` (ON DELETE SET NULL). Populated when this action records a supplement publication (Bill Analysis / Bill Report / Fiscal Note / Bill Summary). Pair with `primary_classification='supplement_published'`. Provides the action log → artifact traceability. |

**Natural-key UNIQUE:** `(bill_id, source, source_action_id)`.

**Multi-target referral decomposition (v1.3, 2026-05-29).** When a source describes a single action that targets multiple bodies — e.g., WSL "Referred to Health and Ways and Means", or OCD's `BillActionRelatedEntity` rows attached to one action — adapters emit **one `BillAction` row per target**, each with its own `acting_organization_id`. This decomposition is the chosen alternative to a 1:N `bill_action_related_entities` child table; it keeps the action log flat and makes "all actions that referred to Ways and Means" a direct query on `acting_organization_id` rather than a join. `display_order` ties the decomposed actions back together in source-intended sequence.

### `canonical.bill_action_classifications`

**New in v1.** 1:N child table for the OCD-style multi-classification of a single BillAction (OCD permits an action to be simultaneously e.g. `reading-3` and `passage`).

| Column | Type | Notes |
|---|---|---|
| `bill_action_id` | ULID NOT NULL FK | |
| `classification` | text(64) NOT NULL | One of OCD's `BILL_ACTION_CLASSIFICATIONS` values. |

**Natural-key UNIQUE:** `(bill_action_id, classification)`.

### `canonical.bill_versions`

**v1.2 (2026-05-28)** grew per-version `text`, `short_description`, and amendment-provenance columns. A BillVersion is now a full record of a single state of the bill, not just metadata.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `version_type` | text(64) NOT NULL | Source vocab: `introduced` / `substitute` / `engrossed` / `first_engrossed` / `enrolled` / `act` / `conference_substitute` / etc. |
| `short_description` | text nullable | **v1.2.** Per-version summary (e.g., OCD `BillAbstract` for this version). |
| `text` | text nullable | **v1.2.** Canonical plain-text representation of the bill at this version. Alternative representations live in `bill_version_links`. Canonicalization rules (MIME handling, OCR, styled-vs-plain) are an open design question — see Open issues. |
| `version_at` | timestamptz nullable | |
| `is_current` | bool NOT NULL default false | At most one current per bill — `Bill.current_version_id` is the denormalized fast path. |

### `canonical.amendments`

**v1.2 (2026-05-28)** added `amendment_kind` to distinguish WA's three kinds of amendments.

| Column | Type | Notes |
|---|---|---|
| `bill_version_id` | ULID NOT NULL FK | **v1.3 (2026-06-02).** FK to `canonical.bill_versions.id` — every amendment is against a specific version. For traditional amendments: points to the current version being amended. For substitute / striking: points to the **newly proposed** BillVersion (which exists as a row from the moment the substitute is offered). Replaces the prior `bill_id` direct FK. |
| `label` | text(64) NOT NULL | "Amendment 1", "Striking Amendment 21", etc. |
| `amendment_kind` | text(16) NOT NULL | **v1.2.** `traditional` (edits) / `striking` (preamble "strike everything after the enacting clause" — effectively a new full version) / `substitute` (overt full replacement, may include new title). |
| `sponsor_person_id` | ULID nullable FK | |
| `sponsor_organization_id` | ULID nullable FK | For committee-offered amendments (federal Rules Committee, etc.). |
| `status` | text(32) NOT NULL | `offered` / `adopted` / `rejected` / `withdrawn` / `pending` / `tabled`. |
| `offered_at` | timestamptz nullable | |
| `adopted_at` | timestamptz nullable | |
| `rejected_at` | timestamptz nullable | |
| `withdrawn_at` | timestamptz nullable | |

**Natural-key UNIQUE:** standard.

**WA practice note (v1.3, 2026-06-02 update):** Striking amendments and proposed substitutes are pragmatically full bill replacements. The schema models this as: a substitute / striking amendment **points to a newly proposed BillVersion** via `Amendment.bill_version_id`. That BillVersion row exists from the moment the substitute is offered, with the proposed text on `BillVersion.text`; adoption merely flips `is_current` and updates `Bill.current_version_id`. **Document identifiers** on the proposal flow naturally: the WA Code Reviser issues one identifier (`H-0734.1/25`) that legitimately attaches to both the Amendment row AND the BillVersion row (the document_identifiers UNIQUE was relaxed in this revision to allow this — see Changelog). Traditional amendments produce no separate BillVersion — they edit the current version's text in place via the engrossment process. Votes on amendments (including strikers/substitutes) always target the `Amendment` row, not the BillVersion.

### `canonical.bill_titles` (new in v1.1)

Bill titles are **multi-valued and lifecycle-dynamic** in every system surveyed: OCD models `BillTitle` + `BillOtherTitle` (with `classification`); LegiScan exposes `title` + `description` separately; uscongress maintains a `titles` array (with `type`, `chamber`, `as`). In WA, **title changes can be traced to specific amendments** — and the procedural significance is load-bearing: an amendment that proposes content outside the bill's current title can be procedurally challenged for exceeding scope. So our model needs not just multiple titles per bill, but title-to-amendment provenance.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `title_text` | text NOT NULL | The title string. |
| `title_type` | text(32) NOT NULL | One of: `canonical` / `short` / `popular` / `official` / `display` / `alternative` / `long` / `summary_title`. Drawn from OCD's `BillTitle.classification` vocab; LegiScan and uscongress map onto this. |
| `chamber_id` | ULID nullable FK | **v1.3 (2026-06-02).** FK to `canonical.organizations.id` for the chamber Org when the title is chamber-specific (federal: House vs. Senate short title); null otherwise. Replaces the `chamber: text(16)` enum column. |
| `as_of_action` | text(64) nullable | When in the lifecycle the title applies — `introduced`, `engrossed`, `enrolled`, `committee_substitute`, etc. uscongress `titles[].as` directly populates this. |
| `language_code` | text(8) nullable | BCP-47 language code for multilingual titles. Null = unspecified (most WA usage). |
| `amendment_id` | ULID nullable FK | **WA-specific.** When an amendment introduced or changed this title, points to the amendment. Null when the title was set at bill introduction. |
| `effective_at` | timestamptz nullable | When this title became active. |
| `replaced_at` | timestamptz nullable | When this title was superseded by a newer one (null = still current). |
| `is_current` | bool NOT NULL default false | Denormalized — at most one row per `(bill_id, title_type, chamber, language_code)` is current. Adapter maintains. |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

**Denormalization:** `Bill.title` always reflects the row where `title_type='canonical' AND is_current=true`. Adapters update both atomically; readers can skip the join.

**Future "killer feature" parking spot:** with `amendment_id` populated for title changes and `Amendment.amendment_text` available, a scope-compatibility scorer could assess whether a proposed amendment's content falls within the bill's current/proposed title — supporting procedural-challenge tooling. Out of MVP scope; explicitly noted because the schema is shaped to enable it.

### `canonical.bill_version_links` (new in v1.2)

A single bill version often exists in multiple forms — original PDF, scraped HTML, OCR'd text from an image PDF, processed git-friendly text, redline against the prior version, etc. This table holds all of them; `BillVersion.text` is the canonical plain-text view that the query layer reads by default.

| Column | Type | Notes |
|---|---|---|
| `bill_version_id` | ULID NOT NULL FK | |
| `url` | text NOT NULL | The link target. |
| `mime_type` | text(128) nullable | The link's content type (text/html, application/pdf, etc.). |
| `kind` | text(32) NOT NULL | `text` / `html` / `pdf` / `xml` / `image_pdf` / `processed_text` / `redline` / `other`. |
| `title` | text nullable | Optional human label. |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

### `canonical.bill_statutory_citations` (new in v1.2)

Statutory citations extracted from a bill version's text. OCD's `Bill.citations` carries the same concept. Useful for queries like "which bills reference RCW 46.16.005?" without scanning bill text at query time. Extraction happens during normalization (P1b enrichment).

| Column | Type | Notes |
|---|---|---|
| `bill_version_id` | ULID NOT NULL FK | Citations belong to a specific version, not the bill as a whole. |
| `statute_section_id` | ULID nullable FK | Resolved citation target. Null when the citation couldn't be resolved (e.g., references a future section, or a non-RCW source). |
| `raw_text` | text(256) NOT NULL | The citation as it appeared in the bill text — e.g., `"RCW 46.16.005"`. |
| `text_offset_start` | int nullable | Optional position-in-text for highlighting / context extraction. |
| `text_offset_end` | int nullable | |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

### `canonical.bill_supplements` (new in v1.3)

Per-`bill_version` supplementary documents authored by non-partisan committee staff or regulatory agencies. WA practice attaches four distinct kinds; the `supplement_kind` enum discriminates them.

| Column | Type | Notes |
|---|---|---|
| `bill_version_id` | ULID NOT NULL FK | Always references a specific version, never just the bill. |
| `bill_id` | ULID NOT NULL FK | Denormalized for cheap "all supplements for bill X" queries. |
| `supplement_kind` | text(32) NOT NULL | `bill_analysis` / `bill_report` / `fiscal_note` / `bill_summary` / `other`. |
| `status` | text(16) nullable | For `fiscal_note` only: `partial` (some agencies responded) / `final` (all agencies responded). Null for other kinds. |
| `revision_sequence` | int NOT NULL default 1 | Multiple PDFs under the same `(bill_version, kind, status)` are distinguished by this counter. |
| `title` | text(512) nullable | E.g., "Senate Bill Report ESHB 1141 (As Reported by Senate Committee on Labor and Commerce)". |
| `author_organization_id` | ULID nullable FK | The committee, agency, or chamber that authored the supplement. |
| `published_at` | timestamptz NOT NULL | |
| `url` | text(2048) nullable | Source PDF (or HTML) URL. |
| `mime_type` | text(128) nullable | |
| `text` | text nullable | Extracted plain text — P1b enrichment populates. |
| `structured_data` | jsonb nullable | Extracted Q&A responses, fiscal-impact tabular data — P1b enrichment. |
| `archival_url` | text(2048) nullable | Archiver sibling URL once sidecar push completes. |
| `archived_at` | timestamptz nullable | When the sidecar push to Archiver completed. |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`. Plus content-key UNIQUE `(bill_version_id, supplement_kind, status, revision_sequence)` so re-fetches don't duplicate.

**Kind taxonomy (WA-anchored, cross-source compatible).**

- **`bill_analysis`** — pre-hearing summary + history composed by non-partisan committee staff *before* a particular bill version is heard or acted on in that committee. Includes hearing details and may mention positions of individuals / orgs (cross-linked to PDC lobbying data via Citation rows).
- **`bill_report`** — post-hearing version of the same shape, composed *after* the version has been heard or acted on. Structurally near-identical to `bill_analysis`; temporally distinguished.
- **`fiscal_note`** — fiscal-impact report by regulatory agencies. May be single-agency or aggregate across agencies. `status` distinguishes `partial` (some agencies have responded) from `final` (all have responded). Multiple revisions per status are distinguished by `revision_sequence` and `published_at`. Always includes tabular data (years × dollar amounts × FTE) and written Q&A responses to a standard array of questions — captured in `structured_data` JSONB post P1b extraction.
- **`bill_summary`** — brief bill description by non-partisan chamber staff (or by an agency / org), usually published at chamber hand-off.

**Lifecycle / event-stream integration.** Each supplement publication generates a paired `BillAction` row whose `primary_classification='supplement_published'` and whose `BillAction.supplement_id` FK points back to the authoritative `bill_supplements` row. The action log query stays cheap (no join required for "what happened on this bill?"), and `BillAction.supplement_id` provides traceability to the artifact.

**Multi-source ingestion.** WSL primary, agency websites (fiscal notes), and LegiScan `Supplement` endpoint all surface these documents. Same `bill_version_id` may carry rows from multiple `source` values; reconciliation is per-`supplement_kind` per `revision_sequence` per source-of-truth policy (WSL wins for committee-authored kinds; agency wins for its own fiscal notes).

**Sidecar archival to Archiver.** Pattern mirrors the identity → Power Map pattern: (1) adapter writes the metadata row + downloads PDF locally; (2) sidecar pushes PDF to Archiver sibling and populates `archival_url` + `archived_at`; (3) once archival completes, local PDF copy can age out per cache policy while metadata + extracted text persist for query. The Archiver becomes the long-term system of record for the PDF binary; usa-wa stays the query layer.

### `canonical.bill_subjects` (new in v1)

Subjects / policy areas / topics a bill addresses. OCD and LegiScan both expose this; queries like "what bills are on cannabis policy this session?" use it directly.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `subject` | text(128) NOT NULL | Source-vocab subject string. |
| `is_primary` | bool NOT NULL default false | |

**Natural-key UNIQUE:** `(bill_id, subject)`.

### `canonical.bill_relationship_types` (new in v1.3, 2026-06-02)

Lookup table for bill-to-bill relationship kinds. Replaces the inline `BillRelationship.relationship_type: text(32)` enum. Encodes the `symmetric` property once per type rather than relying on per-relationship documentation.

| Column | Type | Notes |
|---|---|---|
| `jurisdiction_id` | text(32) NOT NULL | |
| `code` | text(32) NOT NULL | `companion` / `replaces` / `replaced_by` / `related_to` / `prior_session_carryover` / `derived_from` / `other`. |
| `display_name` | text(64) NOT NULL | |
| `symmetric` | bool NOT NULL default false | True for relations like `companion` where A↔B has the same semantic; false for directed relations like `replaces` where A→B and B→A differ. Query layer uses this to materialize the reverse view of symmetric rows without storing both directions. |
| `description` | text nullable | |

**Natural-key UNIQUE:** `(jurisdiction_id, code)`.

### `canonical.bill_relationships` (new in v1)

OpenStates / LegiScan / WSL all surface bill-to-bill relationships. Common in WA where House and Senate companion bills move in parallel.

| Column | Type | Notes |
|---|---|---|
| `from_bill_id` | ULID NOT NULL FK | |
| `to_bill_id` | ULID NOT NULL FK | |
| `relationship_type_id` | ULID NOT NULL FK | **v1.3 (2026-06-02).** FK to `canonical.bill_relationship_types.id`. Replaces the inline `relationship_type: text(32)` enum. The lookup carries the `symmetric` flag. |
| `notes` | text nullable | |

**Natural-key UNIQUE:** `(from_bill_id, to_bill_id, relationship_type_id)`. Symmetric relationships are stored once (from < to lexicographically) with the query layer materializing the reverse view if needed.

### `canonical.bill_events` (new in v1, replaces P0's skeletal Hearing)

Scheduled events on a bill — public hearings, work sessions, executive sessions, calendar slots.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID nullable FK | Nullable: some events (e.g., committee meeting) cover multiple bills via `bill_event_bills` (TBD if needed in P1a). |
| `organization_id` | ULID nullable FK | The committee or chamber holding the event. |
| `event_type` | text(32) NOT NULL | `public_hearing` / `executive_session` / `work_session` / `committee_meeting` / `floor_calendar` / `other`. |
| `scheduled_at` | timestamptz NOT NULL | |
| `ended_at` | timestamptz nullable | |
| `location` | text nullable | Free text room/venue. |
| `status` | text(32) NOT NULL | `scheduled` / `completed` / `cancelled` / `continued` / `rescheduled`. |
| `description` | text nullable | |

**Natural-key UNIQUE:** standard.

## Vote cluster

### `canonical.vote_events`

| Column | Type | Notes |
|---|---|---|
| `subject_type` | text(16) NOT NULL | `bill` / `amendment` / `motion` / `nomination`. **v1.3 (2026-06-01)** added `nomination` for WA Senate confirmation of gubernatorial appointments (and the federal Senate analog of presidential nominations / treaty ratifications). |
| `subject_id` | ULID NOT NULL | Polymorphic; no DB-level FK. When `subject_type='nomination'`, points to the nominee Person until a dedicated Nomination / Appointment entity lands in P1b. |
| `bill_id` | ULID nullable FK | Denormalized for query speed. |
| `amendment_id` | ULID nullable FK | Set when subject_type=amendment. |
| `originating_bill_action_id` | ULID nullable FK | **v1.3 (2026-05-29).** FK to `canonical.bill_actions.id`. The `BillAction` row this vote produced or resulted from — the OCD `VoteEvent.bill_action` analog. Populated when the source surfaces the linkage; null otherwise. |
| `motion_description` | text nullable | When subject_type=motion. |
| `context_type` | text(16) NOT NULL | `floor` / `committee`. |
| `context_organization_id` | ULID NOT NULL FK | |
| `chamber` | text(16) nullable | Denormalized; null for joint sessions. |
| `category` | text(32) nullable | **v1.** Procedural-vs-substantive distinction: `passage` / `cloture` / `recommit` / `tabling` / `motion_to_proceed` / `nomination` / `treaty` / `conviction` / `procedural` / `other`. Federal `vote.category` directly populates this; WA has fewer values but the column generalizes cleanly. |
| `event_at` | timestamptz NOT NULL | |
| `outcome` | text(32) NOT NULL | `passed` / `failed` / `tabled` / `withdrawn` / `inconclusive` / `other`. |

**Natural-key UNIQUE:** standard.

### `canonical.vote_counts`

| Column | Type | Notes |
|---|---|---|
| `vote_event_id` | ULID NOT NULL FK | |
| `count_type` | text(16) NOT NULL | `yea` / `nay` / `nay_without_rec` / `nay_do_not_pass` / `excused` / `absent` / `present_not_voting` / `paired` / `other`. WA committee-report votes distinguish two nay variants (v1.3, 2026-05-31): `nay_without_rec` (without recommendation — lighter rejection) and `nay_do_not_pass` (DNP — stronger rejection). Plain `nay` remains the floor-vote default. |
| `value` | int NOT NULL | |

**Natural-key UNIQUE:** `(vote_event_id, count_type)`. `paired` added v1 for OCD round-trip; rare in WA but valid Senate behavior elsewhere.

### `canonical.person_votes`

| Column | Type | Notes |
|---|---|---|
| `vote_event_id` | ULID NOT NULL FK | |
| `person_id` | ULID nullable FK | **v1: now nullable.** When resolution to a known Person hasn't completed yet, `voter_name_raw` is populated instead. |
| `voter_name_raw` | text(256) nullable | **v1.** Source-provided voter name pending ID resolution. |
| `vote` | text(16) NOT NULL | Aligned with `vote_counts.count_type`: `yea` / `nay` / `nay_without_rec` / `nay_do_not_pass` / `abstain` / `excused` / `absent` / `present_not_voting` / `paired`. WA committee-report nay variants per `vote_counts.count_type` notes. |

**CHECK constraint:** `person_id IS NOT NULL OR voter_name_raw IS NOT NULL`.

**Natural-key UNIQUE:** `(vote_event_id, person_id)` partial index where `person_id IS NOT NULL`.

## Statute cluster (unchanged from P0)

The five statute-cluster tables from P0 remain as-designed: `StatuteCode`, `StatuteTitle`, `StatuteChapter`, `StatuteSection`, `BillStatuteChange`. See [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md) §Statute corpus cluster.

## PDC cluster

### `canonical.lobbying_activities`

| Column | Type | Notes |
|---|---|---|
| `person_id` | ULID nullable FK | Individual lobbyist. |
| `organization_id` | ULID nullable FK | Lobby firm. |
| `employer_organization_id` | ULID nullable FK | |
| `period_start` | date NOT NULL | |
| `period_end` | date NOT NULL | |
| `compensation` | numeric(14,2) nullable | |
| `expenses` | numeric(14,2) nullable | |

**CHECK constraint:** `person_id IS NOT NULL OR organization_id IS NOT NULL`.

### `canonical.lobbying_positions`

| Column | Type | Notes |
|---|---|---|
| `lobbying_activity_id` | ULID NOT NULL FK | |
| `bill_id` | ULID nullable FK | Null when bill-reference resolver couldn't find a match. |
| `bill_reference_raw` | text(128) nullable | |
| `position` | text(16) NOT NULL | `support` / `oppose` / `neutral`. |

**Natural-key UNIQUE:** `(lobbying_activity_id, bill_id)`.

### `canonical.contributions`

| Column | Type | Notes |
|---|---|---|
| `recipient_organization_id` | ULID NOT NULL FK | |
| `contributor_person_id` | ULID nullable FK | |
| `contributor_organization_id` | ULID nullable FK | |
| `contributor_name_raw` | text(512) nullable | |
| `amount` | numeric(14,2) NOT NULL | |
| `contributed_at` | timestamptz NOT NULL | |

**CHECK constraint:** at most one of `contributor_person_id` / `contributor_organization_id` is non-null.

## Unavoidable lossy directions

These are losses we **accept** in the transformation specs. Fixing them would require upstream schema changes or would impose costs out of proportion with the value.

| Direction | What's lost | Why we accept it |
|---|---|---|
| **Our `Amendment` → OCD** | Sponsor, full text, status="pending" (anything except "did this bill get amended?"). | OCD has no Amendment entity; amendments live only as `BillAction.classification ∈ amendment-*`. Round-tripping requires OCD upstream changes. We emit best-effort `BillAction` rows when exporting to OCD. |
| **OCD `Bill.classification` (array) → our `Bill.bill_type` (scalar)** | Multi-classified bills (e.g., resolution + concurrent + appropriation) collapse. | OCD's permissive multi-class shape diverges from WA reality where bills carry exactly one type. We keep scalar; if a multi-class jurisdiction is added later, revisit. |
| **LegiScan `VoteEvent` → our polymorphic vote subject/context** | LegiScan only collects floor votes on bills. Committee votes, amendment votes, motion votes are unreachable from LegiScan-sourced data. | Use WSL SOAP as primary for vote data; LegiScan only as corroboration on floor-vote-on-bill. |
| **LegiScan `Amendment` → our Amendment** | Sponsor IDs, lifecycle granularity (offered/pending/withdrawn). LegiScan has only `adopted: 0|1` + a single `date`. | LegiScan amendments are corroboration-only; WSL is authoritative for amendment data. |
| **uscongress floor-only votes** | Federal upstream doesn't collect committee votes at all. | Structural data gap in the federal upstream, not a schema gap. Federal sibling deployment (`usa-fed-api`) would need its own primary source for committee votes — likely scraping committee websites. |
| **uscongress current-only committee membership** | Pre-current committee chair / ranking-member history. | `congress-legislators/committee-membership-current.yaml` is current-state-only. Federal historical committee membership would need a different primary source or a periodic snapshot job. |
| **OCD `Person.identifiers` array (rich) → our `person_identifiers`** | None — v1's child table fully round-trips OCD's. (This direction is **not** lossy after v1.) | Resolved by v1 §External identifiers. |
| **OCD `VOTE_OPTION='paired'`** | Resolved by v1's addition of `paired` to vote_counts.count_type and person_votes.vote. | Resolved. |

## Provenance integration

Every entity in this spec writes Citation rows through `clearinghouse_core.runner.AdapterRunner`. Polymorphic Citation references the entity by `(entity_type, entity_id)`:

`entity_type` values (snake_case table names): `person`, `organization`, `role`, `assignment`, `person_identifier`, `organization_identifier`, `bill`, `bill_type`, `bill_sponsorship`, `bill_action`, `bill_action_classification`, `bill_version`, `amendment`, `bill_title`, `bill_subject`, `bill_relationship`, `bill_relationship_type`, `bill_event`, `bill_supplement`, `vote_event`, `vote_count`, `person_vote`, `lobbying_activity`, `lobbying_position`, `contribution`, `statute_code`, `statute_title`, `statute_chapter`, `statute_section`, `bill_statute_change`, `legislative_session`.

Denormalized `primary_source_id`, `last_fetched_at`, `last_fetch_event_id` columns on every entity (per Universal entity shape) carry single-row citations cheaply; explicit field-level provenance via the `citations` table only when meaningfully needed.

## Vocabulary status

This is **v1 final**. Vocabularies have been pressure-tested against three foreign schemas:

- `Bill.current_status` — source-vocab text (kept) + `Bill.current_status_class` (normalized).
- `BillAction.action_type` — source-vocab text + `BillAction.primary_classification` + 1:N `bill_action_classifications` (OCD-aligned).
- `VoteEvent.outcome` and `VoteEvent.category` — OCD/uscongress aligned.
- `PersonVote.vote` and `VoteCount.count_type` — aligned and symmetric; both include `paired`.
- `Organization.org_type`, `Role.role_type`, `Role.name` — cover state + federal + municipal-friendly cases.
- `Bill.bill_type` — scalar (accepted lossy direction; see Unavoidable lossy directions).
- `BillVersion.version_type` — source vocab; P1a normalization will canonicalize.

## Open issues (forwarded to P1a or implementation)

**Canonical bill-text storage** is the most consequential design discussion remaining. `BillVersion.text` lands as a `text` column in v1.2, but the *canonicalization* rules are open:

- **MIME**: WSL emits PDFs (sometimes image-only PDFs that need OCR). Storing the original byte stream and a plain-text derivation are two different things; the schema doesn't yet say which one `BillVersion.text` is. `BillVersionLink` will hold every form; `BillVersion.text` should be a deliberate, queryable canonical form (probably OCR'd-and-cleaned plain text).
- **Pagination / formatting noise**: legislative bill texts often include page numbers, line numbers, marginal annotations, and chamber-style formatting. For citation extraction and text search, these are noise; for legal precision, sometimes they matter. Stripping them is a normalization choice.
- **Styling**: HTML versions carry markup. Storing styled text in `BillVersion.text` makes diff/search awkward; storing only plain text loses markup-conveyed information (italics for newly added phrases, strikethroughs for deletions).
- **Git-friendly processed representations**: a future text-management pipeline (the "RCW as git" idea sketched in the MVP spec) would benefit from a deliberately-formatted version-control-friendly representation. `BillVersionLink.kind='processed_text'` is reserved for this.

P1b enrichment will produce a concrete proposal for `BillVersion.text` canonicalization. Until then, the column is non-null when a sensible canonical form exists and null otherwise. Adapters always populate `bill_version_links` with whatever source forms they encountered.

---



Items the transformations did **not** resolve and which P1a or later phases must address:

1. **Per-jurisdiction OCD-class mapping table for BillAction.** Mechanically populating `primary_classification` and `bill_action_classifications` from WSL's action vocabulary requires a hand-curated mapping table (which WSL action strings map to which OCD classes). P1a builds this.
2. **Resolution of `*_name_raw` columns.** A periodic resolver job converts raw-name fallback values to FKs. Whether this is per-adapter or a generic component is a P1a implementation choice.
3. **`bill_event_bills` many-to-many.** v1 includes `bill_events.bill_id` as nullable for events covering multiple bills, but doesn't materialize the many-to-many table. Defer to P1a once we know whether WSL reports multi-bill hearings or just one bill per event row.
4. **Federal subscriber sibling.** A future `usa-fed-api` deployment using this IA would surface federal-specific edge cases (Senate continuing-body LegislativeSession semantics, Representative/Delegate/Resident-Commissioner Role distinctions in queries). Not blocking WA; flagged for awareness.
5. **Vote outcome edge cases.** Recommittal motions, motions to table — `outcome=tabled` may need finer-grained verbs. P1a's first vote-normalization pass will produce real edge cases; revisit then.
6. **Anonymous contribution rules.** `contributor_name_raw` is the v1 fallback for unresolved contributors; whether a dedicated `is_anonymous` flag is also needed is a P1c concern.

## Cross-references

- Transformation specs (peers):
  - [`docs/specs/2026-05-27-transformation-ocd.md`](2026-05-27-transformation-ocd.md)
  - [`docs/specs/2026-05-27-transformation-legiscan.md`](2026-05-27-transformation-legiscan.md)
  - [`docs/specs/2026-05-27-transformation-uscongress.md`](2026-05-27-transformation-uscongress.md)
- Inputs:
  - [`docs/research/2026-05-26-multi-state-legislative-ia-delta.md`](../research/2026-05-26-multi-state-legislative-ia-delta.md)
  - [`docs/research/2026-05-26-power-map-integration-contract.md`](../research/2026-05-26-power-map-integration-contract.md)
- Parent:
  - [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md)
- Plan:
  - [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md)
- Upstream feature request:
  - [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156)
- Tracking issue:
  - [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3)
