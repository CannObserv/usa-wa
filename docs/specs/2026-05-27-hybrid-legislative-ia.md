# Hybrid legislative information architecture

- **Date:** 2026-05-27
- **Status:** final (v1 — synthesizes findings from three transformation specs)
- **Scope:** All canonical legislative-domain entities. Supersedes the P0-skeleton entity descriptions in [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md) §Canonical data spine.
- **Tracks:** [GH #3](https://github.com/CannObserv/usa-wa/issues/3); see plan at [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md).
- **Supersedes:** the v0 draft of this file (commit `aed896c`); see Changelog § below.

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
| `Role.district` (text nullable). Moves district context off `Person.current_district`. | OCD #5 | Identity cluster |

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
- `current_district` removed (v0 → v1). District context lives on `Role` now; see `canonical.roles`.

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
| `district` | text(32) nullable | **New in v1.** District/seat identifier (e.g., "21" for WA LD 21; "WA-3" for federal House). Null for at-large or non-district roles. |

**Natural-key UNIQUE:** `(jurisdiction_id, organization_id, name, district)`. Roles for the same chamber-position with different districts are distinct (e.g., Senator-LD21 vs Senator-LD22).

Examples — note the federal-stress-test-driven distinction between Representative / Delegate / Resident Commissioner as separate role names:

- `(org=WA Senate, name="Senator", district="21", role_type="elected_member")`
- `(org=US House, name="Representative", district="WA-3", role_type="elected_member")`
- `(org=US House, name="Delegate", district="DC-AL", role_type="elected_member")` — non-voting
- `(org=US House, name="Resident Commissioner", district="PR-AL", role_type="elected_member")` — 4-year term
- `(org=Senate Health Committee, name="Chair", district=null, role_type="committee_leadership")`

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
| `classification` | text(32) NOT NULL | One of: `regular` / `special` / `sine_die` / `extraordinary` / `other`. |
| `start_date` | date nullable | |
| `end_date` | date nullable | |
| `is_active` | bool NOT NULL default false | |
| `biennium_label` | text(16) nullable | WA-flavored — preserved for round-tripping ("2025-26"). |

**Natural-key UNIQUE:** `(jurisdiction_id, slug)`.

## Bill cluster

### `canonical.bills`

| Column | Type | Notes |
|---|---|---|
| `legislative_session_id` | ULID NOT NULL FK | |
| `originating_chamber` | text(16) NOT NULL | **v1 (was `chamber`).** Body where the bill was first introduced — `house` / `senate` / `unicameral`. |
| `current_chamber` | text(16) nullable | **v1.** Body the bill is currently in. Null when in conference or fully passed both. |
| `number` | int NOT NULL | |
| `bill_type` | text(32) nullable | HB / SB / HJR / SJR / HCR / SCR / HJM / SJM / HR / S / etc. |
| `title` | text NOT NULL | **Denormalized current canonical title** — synced from `bill_titles` where `is_current=true AND title_type='canonical'`. Most queries read this column without joining. |
| `short_description` | text nullable | **Latest bill summary / abstract** from the source (e.g., OCD's `BillAbstract`, LegiScan's `Bill.description`). Single value, updated in place. |
| `current_status` | text(128) nullable | Source-vocabulary text. |
| `current_status_class` | text(32) nullable | **v1.** Normalized to OCD's `bill_action_classification` plus LegiScan's status vocab — values like `introduced` / `in_committee` / `passed_first_chamber` / `passed_second_chamber` / `vetoed` / `signed` / `enacted` / `failed` / `withdrawn`. |
| `current_status_at` | timestamptz nullable | **v1 (replaces `current_step`).** When `current_status` was last updated. |
| `introduced_at` | timestamptz nullable | |
| `enacted_as` | text(64) nullable | **v1.** Public Law / chapter law cross-reference once enacted (federal: "Public Law 119-12"; WA: "Chapter 47, Laws of 2025"). |
| `current_text` | text nullable | Current bill text; version history is `BillVersion`. |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

Notes:
- v0's `current_step` column is dropped. The previous use-cases (status enum + when-was-status-set) are replaced by `current_status_class` + `current_status_at`.
- `Bill.title` is denormalized; the full title history (including amendment-driven changes, alternative titles like short / popular / official / display, chamber-specific titles, and historical replaced titles) lives in `canonical.bill_titles` (added v1.1, post-transformation-review). See below.

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
| `withdrawn_at` | timestamptz nullable | |

**CHECK constraint:** at most one of `person_id` / `organization_id` is non-null, with `sponsor_name_raw` non-null when both are null (pending resolution).

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

### `canonical.bill_actions`

Append-only lifecycle log.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `action_at` | timestamptz NOT NULL | |
| `chamber` | text(16) nullable | `house` / `senate` / null for executive actions. |
| `acting_organization_id` | ULID nullable FK | The body that took the action — chamber, committee, etc. |
| `action_type` | text(64) NOT NULL | Source-vocab text. |
| `primary_classification` | text(64) nullable | **v1.** Single most-canonical OCD class for display: `introduction` / `reading-1` / `passage` / `amendment-passage` / `committee-passage` / `executive-signature` / `veto-override-passage` / etc. The full multi-class array lives in `bill_action_classifications`. |
| `description` | text NOT NULL | Free-text description. |
| `display_order` | int nullable | **v1.** Tie-breaker for same-day actions; preserves source's intended sequence. |
| `is_major` | bool NOT NULL default false | **v1.** Source's "milestone" flag (LegiScan `importance`). |

**Natural-key UNIQUE:** `(bill_id, source, source_action_id)`.

### `canonical.bill_action_classifications`

**New in v1.** 1:N child table for the OCD-style multi-classification of a single BillAction (OCD permits an action to be simultaneously e.g. `reading-3` and `passage`).

| Column | Type | Notes |
|---|---|---|
| `bill_action_id` | ULID NOT NULL FK | |
| `classification` | text(64) NOT NULL | One of OCD's `BILL_ACTION_CLASSIFICATIONS` values. |

**Natural-key UNIQUE:** `(bill_action_id, classification)`.

### `canonical.bill_versions`

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `version_type` | text(64) NOT NULL | Source vocab covering: `introduced` / `substitute` / `engrossed` / `first_engrossed` / `enrolled` / `act` / `conference_substitute` / etc. LegiScan's 14-value `TextType` is the inspiration; we accept the broader source vocab but P1a normalization will canonicalize. |
| `version_at` | timestamptz nullable | |
| `is_current` | bool NOT NULL default false | |

### `canonical.amendments`

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `label` | text(64) NOT NULL | "Amendment 1", "Striking Amendment 21", etc. |
| `amendment_text` | text nullable | |
| `sponsor_person_id` | ULID nullable FK | |
| `sponsor_organization_id` | ULID nullable FK | For committee-offered amendments (federal Rules Committee, etc.). |
| `status` | text(32) NOT NULL | `offered` / `adopted` / `rejected` / `withdrawn` / `pending` / `tabled`. |
| `offered_at` | timestamptz nullable | |
| `adopted_at` | timestamptz nullable | |
| `rejected_at` | timestamptz nullable | |
| `withdrawn_at` | timestamptz nullable | |

**Natural-key UNIQUE:** standard.

### `canonical.bill_titles` (new in v1.1)

Bill titles are **multi-valued and lifecycle-dynamic** in every system surveyed: OCD models `BillTitle` + `BillOtherTitle` (with `classification`); LegiScan exposes `title` + `description` separately; uscongress maintains a `titles` array (with `type`, `chamber`, `as`). In WA, **title changes can be traced to specific amendments** — and the procedural significance is load-bearing: an amendment that proposes content outside the bill's current title can be procedurally challenged for exceeding scope. So our model needs not just multiple titles per bill, but title-to-amendment provenance.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `title_text` | text NOT NULL | The title string. |
| `title_type` | text(32) NOT NULL | One of: `canonical` / `short` / `popular` / `official` / `display` / `alternative` / `long` / `summary_title`. Drawn from OCD's `BillTitle.classification` vocab; LegiScan and uscongress map onto this. |
| `chamber` | text(16) nullable | When the title is chamber-specific (federal: House vs. Senate short title); null otherwise. |
| `as_of_action` | text(64) nullable | When in the lifecycle the title applies — `introduced`, `engrossed`, `enrolled`, `committee_substitute`, etc. uscongress `titles[].as` directly populates this. |
| `language_code` | text(8) nullable | BCP-47 language code for multilingual titles. Null = unspecified (most WA usage). |
| `amendment_id` | ULID nullable FK | **WA-specific.** When an amendment introduced or changed this title, points to the amendment. Null when the title was set at bill introduction. |
| `effective_at` | timestamptz nullable | When this title became active. |
| `replaced_at` | timestamptz nullable | When this title was superseded by a newer one (null = still current). |
| `is_current` | bool NOT NULL default false | Denormalized — at most one row per `(bill_id, title_type, chamber, language_code)` is current. Adapter maintains. |

**Natural-key UNIQUE:** standard `(jurisdiction_id, source, source_id)`.

**Denormalization:** `Bill.title` always reflects the row where `title_type='canonical' AND is_current=true`. Adapters update both atomically; readers can skip the join.

**Future "killer feature" parking spot:** with `amendment_id` populated for title changes and `Amendment.amendment_text` available, a scope-compatibility scorer could assess whether a proposed amendment's content falls within the bill's current/proposed title — supporting procedural-challenge tooling. Out of MVP scope; explicitly noted because the schema is shaped to enable it.

### `canonical.bill_subjects` (new in v1)

Subjects / policy areas / topics a bill addresses. OCD and LegiScan both expose this; queries like "what bills are on cannabis policy this session?" use it directly.

| Column | Type | Notes |
|---|---|---|
| `bill_id` | ULID NOT NULL FK | |
| `subject` | text(128) NOT NULL | Source-vocab subject string. |
| `is_primary` | bool NOT NULL default false | |

**Natural-key UNIQUE:** `(bill_id, subject)`.

### `canonical.bill_relationships` (new in v1)

OpenStates / LegiScan / WSL all surface bill-to-bill relationships. Common in WA where House and Senate companion bills move in parallel.

| Column | Type | Notes |
|---|---|---|
| `from_bill_id` | ULID NOT NULL FK | |
| `to_bill_id` | ULID NOT NULL FK | |
| `relationship_type` | text(32) NOT NULL | `companion` (symmetric) / `replaces` / `replaced_by` / `related_to` / `prior_session_carryover` / `derived_from` / `other`. |
| `notes` | text nullable | |

**Natural-key UNIQUE:** `(from_bill_id, to_bill_id, relationship_type)`. Symmetric relationships are stored once (from < to lexicographically) with the query layer materializing the reverse view if needed.

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
| `subject_type` | text(16) NOT NULL | `bill` / `amendment` / `motion`. |
| `subject_id` | ULID NOT NULL | Polymorphic; no DB-level FK. |
| `bill_id` | ULID nullable FK | Denormalized for query speed. |
| `amendment_id` | ULID nullable FK | Set when subject_type=amendment. |
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
| `count_type` | text(16) NOT NULL | `yea` / `nay` / `excused` / `absent` / `present_not_voting` / `paired` / `other`. |
| `value` | int NOT NULL | |

**Natural-key UNIQUE:** `(vote_event_id, count_type)`. `paired` added v1 for OCD round-trip; rare in WA but valid Senate behavior elsewhere.

### `canonical.person_votes`

| Column | Type | Notes |
|---|---|---|
| `vote_event_id` | ULID NOT NULL FK | |
| `person_id` | ULID nullable FK | **v1: now nullable.** When resolution to a known Person hasn't completed yet, `voter_name_raw` is populated instead. |
| `voter_name_raw` | text(256) nullable | **v1.** Source-provided voter name pending ID resolution. |
| `vote` | text(16) NOT NULL | Aligned with `vote_counts.count_type`: `yea` / `nay` / `abstain` / `excused` / `absent` / `present_not_voting` / `paired`. |

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

`entity_type` values (snake_case table names): `person`, `organization`, `role`, `assignment`, `person_identifier`, `organization_identifier`, `bill`, `bill_sponsorship`, `bill_action`, `bill_action_classification`, `bill_version`, `amendment`, `bill_subject`, `bill_relationship`, `bill_event`, `vote_event`, `vote_count`, `person_vote`, `lobbying_activity`, `lobbying_position`, `contribution`, `statute_code`, `statute_title`, `statute_chapter`, `statute_section`, `bill_statute_change`, `legislative_session`.

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
