# Jurisdictional IA — Power Map extension + usa-wa consumer integration

- **Date:** 2026-05-31
- **Status:** in implementation — refreshed 2026-06-01 against PM's Phase 1 + Phase 2 of #168 (5 read routes + `POST /api/v1/jurisdictions/observations` write endpoint shipped). See Changelog below for the design-vs-shipped delta.
- **Scope:** PM data-model extension for Jurisdictions (new entity type alongside identity), the usa-wa consumer integration shape, and the schema-wide `jurisdiction_id: text(32)` → `ULID FK` refactor that follows from it.
- **Tracks:** [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3); upstream coordination via [CannObserv/power-map#168](https://github.com/CannObserv/power-map/issues/168) (filed 2026-05-31).
- **Inputs:**
  - External exploration: [`docs/research/2026-05-30-jurisdictional-information-architecture.md`](../research/2026-05-30-jurisdictional-information-architecture.md)
  - Existing PM integration: [`docs/specs/2026-05-27-power-map-integration.md`](2026-05-27-power-map-integration.md)
  - Hybrid IA: [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)
  - PM upstream sub-issues: [#162](https://github.com/CannObserv/power-map/issues/162) (observation conflict-resolution), [#164](https://github.com/CannObserv/power-map/issues/164) (`POST /observations`)

## Changelog (2026-06-01 — PM #168 Phase 1 + Phase 2 shipped)

Power Map shipped the Jurisdiction extension on 2026-06-01 across two phases. PM-side design review surfaced several refinements; below is the design-vs-shipped delta. The spec body below has been updated inline to reflect what actually exists in PM.

| Item | Spec proposal | PM shipped |
|---|---|---|
| `type` column shape | Free-text `text(32)` | **FK to `jurisdiction_types` lookup** (16 rows seeded) |
| Relationship-type vocabulary | 12 codes | **11 codes** — `exercises_concurrent_jurisdiction` dropped (LEA-specific; reserve slot) |
| Symmetric column name | `symmetric` | `is_symmetric` |
| `pm.jurisdiction_identifiers` table | Separate polymorphic table | **Reused existing `identifiers` table** — `entity_type` CHECK extended on 7 tables; `jur_ocd` / `jur_fips` / `jur_iso3166_2` seeded into `entity_identifier_types` |
| Bitemporal column naming | `valid_start` / `valid_end` / `transaction_start` / `transaction_end` | `valid_from` / `valid_until` / `recorded_at` / `superseded_at` (PM has no bitemporal precedent; their proposal accepted since usa-wa had no code written) |
| Write endpoint URL | `POST /api/v1/observations` with `entity_type: "jurisdiction"` | **`POST /api/v1/jurisdictions/observations`** — per-entity endpoint (establishes pattern for follow-on #169 to decompose `/observations` into per-entity routes) |
| Read endpoint filter param | `archived` | `include_archived` (consistency with orgs/people endpoints) |
| Pre-seed bootstrap path | Through observation flow | **PM-owned admin import** — usa-wa's `initial_jurisdictions.json` becomes PM's admin-imported seed file; subsequent usa-wa observations route against anchored IDs (`AUTO_ATTACHED` from day one) |

**Net effect on usa-wa work:** sidecar (both read and write flows) is no longer blocked. Local schema work in this spec/plan is unchanged in shape; the cache mirror simply adopts PM's column names and adds a `jurisdiction_types` lookup table. The original `clearinghouse_core.jurisdiction_identifiers` table is **dropped from this scope** — local identifier caching is deferred to the sidecar follow-up plan; for usa-wa MVP, identifiers are resolved via PM API when needed.

## Problem

usa-wa's existing jurisdiction representation is unanchored and underspecified:

- `jurisdiction_id: text(32)` slugs (`"usa-wa"`) used across ~30 canonical tables — partition tags with no referential integrity, no hierarchy, no temporal validity.
- `Role.district: text(32)` — the LD-21 reference is just a label. No way to ask "what bills did the Senator from LD 21 sponsor in the 2022 cycle?" without text matching, and no way to handle redistricting cleanly.
- `clearinghouse_core.Jurisdiction` exists (slug / name / level enum) but has 4 type values and no relationship model.

The data is **swiffy** — vague enough to ship MVP, but it'll break the moment usa-wa needs to handle the 2030 redistricting cycle, or a federal sibling needs to share identity data with usa-wa across containing jurisdictions.

External exploration (see research note) converged on a Component/Tags graph model with bitemporal versioning. The brainstorm narrowed scope to **Power Map as the system of record** (extending PM's existing identity scope), graph + bitemporal in MVP, spatial geometry deferred, and a **producer/archival sidecar** mirroring the identity pattern.

## Decision summary

| Decision | Choice |
|---|---|
| Service placement | **Extend Power Map** (not a new sibling, not usa-wa-local) |
| MVP scope | Graph (Component/Tags model) + bitemporal; defer spatial geometry + Cycles/Scenarios |
| Role FK shape | Full schema-wide refactor — every `jurisdiction_id: text(32)` → `ULID FK` to local cache; `Role.district` dropped |
| Consumer pattern | Producer/archival sidecar, mirroring identity (usa-wa is both producer and consumer; local cache as fast-path read) |
| Slug convention | `<iso-3166-1-alpha3>-<iso-3166-2>` base (`usa-wa`) + `<type>-<value>` suffixes (`usa-wa-county-king`, `usa-wa-ld-21`); multi-layer-able; external taxonomies (OCD, FIPS, etc.) as 1:N `JurisdictionIdentifier` records |
| Write API shape | Observation pattern at `POST /api/v1/jurisdictions/observations` (per-entity URL — PM established the pattern for follow-on #169 decomposing the shared `/observations` into per-entity routes) |
| Disposition vocabulary | `NEW` / `AUTO_ATTACHED` / `REJECTED` (uppercase per PM impl; PM had discarded `queued-for-review` in design review) |
| Bitemporal column naming | `valid_from` / `valid_until` / `recorded_at` / `superseded_at` (PM's proposal accepted) |

## Section 1 — Power Map data model extension

PM ships three new tables + reuses two existing ones (`identifiers`, `entity_identifier_types`) by extending their `entity_type` CHECK to include `'jurisdiction'`.

### `pm.jurisdictions` (the entity)

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `slug` | text, UNIQUE | Pattern: `<iso-3166-1-alpha3>-<iso-3166-2>[-<key>-<value>]*`. See "Slug convention" below for formatting rules. Examples: `usa`, `usa-wa`, `usa-wa-ld-21`, `usa-wa-county-king`, `usa-wa-city-seattle`. |
| `name` | text NOT NULL | Display name. |
| `type_id` | ULID FK → `pm.jurisdiction_types` | **Changed from `type: text` to FK in PM's implementation** (consistent with usa-wa's `bill_types` resolution; eliminates free-text drift). |
| `valid_from` | timestamptz nullable | When the jurisdiction is legally active in the real world. **Was `valid_start` in original proposal.** |
| `valid_until` | timestamptz nullable | Null = currently active. **Was `valid_end`.** Exclusive upper bound (reads naturally in range predicates). |
| `recorded_at` | timestamptz NOT NULL | When the record was added to PM (audit + "undo" semantics). **Was `transaction_start`.** |
| `superseded_at` | timestamptz nullable | Null = current row; non-null = superseded by a later transaction. **Was `transaction_end`.** |

#### Slug convention

The slug pattern is `<iso-3166-1-alpha3>-<iso-3166-2>[-<key>-<value>]*`.

- **Base (positional, no key):** the first two segments are the ISO 3166-1 alpha-3 country code followed by the ISO 3166-2 subdivision code (e.g., `usa-wa`). The base alone is a valid slug for the subdivision itself. Country-only slugs (e.g., `usa`) are also valid (no subdivision segment).
- **Suffix pairs (key-value):** every segment after the base is a `<key>-<value>` pair separated from prior segments and from each other by a single ASCII dash (`-`).
- **Character set:** lowercase ASCII `[a-z0-9_]` only. Use a single underscore (`_`) within a key or a value where a space, hyphen, or other non-ASCII / non-alphanumeric character would naturally occur. The dash (`-`) is reserved as the segment separator and never appears within a key or value.
- **Examples:**
  - `usa` — country only.
  - `usa-wa` — state.
  - `usa-wa-county-king` — King County.
  - `usa-wa-county-grays_harbor` — Grays Harbor County (space → underscore).
  - `usa-wa-county-pend_oreille` — Pend Oreille County.
  - `usa-wa-city-seattle` — City of Seattle.
  - `usa-wa-ld-21` — 21st Legislative District (state Senate / House share the LD number).
  - `usa-wa-cd-7` — 7th Congressional District.
- **No nesting in slugs.** Slugs are flat — they describe *what the jurisdiction is*, not *what contains it*. Seattle is `usa-wa-city-seattle`, not `usa-wa-county-king-city-seattle`. The containment graph in `pm.jurisdiction_relationships` carries the "Seattle is in King County" knowledge, and queries that need it traverse the graph rather than parse the slug.
- **Multi-pair slugs.** Allowed when a jurisdiction is naturally identified by more than one key-value pair, e.g., a precinct that takes both a county and a precinct number could be `usa-wa-county-king-precinct-1234`. Use sparingly — when in doubt, prefer a flat slug + a graph relationship.

### `pm.jurisdiction_types` (the type lookup — added in PM impl)

PM resolved the open question "should `type` be a FK lookup?" in favor of the lookup pattern (matching usa-wa's `bill_types` resolution and PM's existing `entity_identifier_types` shape). Seeded with the 16 documented values.

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `slug` | text UNIQUE NOT NULL | One of the documented type values. |
| `display_name` | text NOT NULL | Human-readable label. |
| `created_at` | timestamptz NOT NULL | |

**Initial vocabulary** (16 rows seeded by PM): `country` / `state` / `county` / `city` / `legislative_district` / `legislative_district_upper` / `legislative_district_lower` / `congressional_district` / `judicial_district` / `school_district` / `water_district` / `tribal_nation` / `federal_enclave` / `census_block` / `census_tract` / `other`. `legislative_district` is for jurisdictions where Senate and House boundaries are shared (e.g., WA); `legislative_district_upper` / `legislative_district_lower` distinguish chambers when they have separate boundaries. Adapters add rows without schema change.

### `pm.jurisdiction_relationships` (the graph)

The Component/Tags model lives here.

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `subject_jurisdiction_id` | ULID FK | |
| `object_jurisdiction_id` | ULID FK | |
| `relationship_type_id` | ULID FK | → `pm.jurisdiction_relationship_types` |
| `metadata` | jsonb | Weight percentage, basis, legal reference (URL/statute), etc. |
| `valid_from` / `valid_until` | timestamptz | Real-world validity. |
| `recorded_at` / `superseded_at` | timestamptz | PM-record validity. |

**Natural-key UNIQUE:** `(subject_jurisdiction_id, object_jurisdiction_id, relationship_type_id, valid_from)`.

### `pm.jurisdiction_relationship_types` (the lookup)

Codes from the external exploration, with `is_symmetric` flag.

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `code` | text(64) UNIQUE | See vocab below. |
| `display_name` | text(128) NOT NULL | |
| `category` | text(16) NOT NULL | `spatial` / `governance` / `functional` / `temporal` — for query filtering. |
| `is_symmetric` | bool NOT NULL default false | True for `partially_overlaps`, `is_coterminous_with`; false for directed relations like `is_fully_contained_by`. |
| `description` | text nullable | |

**Initial vocabulary (11 codes — PM dropped `exercises_concurrent_jurisdiction` from MVP per PM design review; reserved for future LEA-tracking cohort needs):**

| Code | Category | is_symmetric |
|---|---|---|
| `is_fully_contained_by` | spatial | no |
| `partially_overlaps` | spatial | yes |
| `is_coterminous_with` | spatial | yes |
| `has_regulatory_authority_over` | governance | no |
| `has_extraterritorial_jurisdiction_over` | governance | no |
| `member_of` | functional | no |
| `reports_to` | functional | no |
| `contracts_services_from` | functional | no |
| `supersedes` | temporal | no |
| `succeeded_by` | temporal | no |
| `evolved_from` | temporal | no |

### Identifiers — reuse existing `identifiers` table (no new table)

**PM's design-review decision (resolved 2026-06-01):** rather than create a separate `pm.jurisdiction_identifiers` table, PM extended the existing polymorphic `identifiers` table to support jurisdictions. The `entity_type` CHECK constraint was extended on 7 existing tables (`identifiers`, `entity_addresses`, `links`, `contact_methods`, `import_provenance`, `field_confidence`, `deleted_entities`) to include `'jurisdiction'`.

Three identifier types seeded into `entity_identifier_types`:

- `jur_ocd` — OpenCivicData division identifier (e.g., `ocd-division/country:us/state:wa/sldu:21`)
- `jur_fips` — Census FIPS code
- `jur_iso3166_2` — ISO 3166-2 subdivision code

Adapters add additional schemes (e.g., `jur_wsl_district_id`) without schema change — same pattern as `person_identifiers` / `organization_identifiers`.

### Deferred (not in MVP)

- **Spatial geometry** — PostGIS `Geometry(POLYGON/MULTIPOLYGON)` columns on `pm.jurisdictions`. Adds the classification (`/resolve`) and aggregation (`/aggregate`) endpoints from the JaaS exploration.
- **Cycles + Scenarios containers** — bitemporal + the lineage relationship types (`supersedes` / `succeeded_by` / `evolved_from`) handle historical-version queries without an explicit Cycle/Scenario wrapper. Add when draft-map comparison becomes a query target (usa-wa won't have one in P1–P2).
- **Aggregation via Census Block "Atomic Units" with areal interpolation** — comes with spatial geometry.

## Section 2 — usa-wa consumer integration

### `clearinghouse_core.jurisdictions` (local cache, replacing the existing minimal entity)

Mirrors PM's `pm.jurisdictions` shape so the cache is a drop-in subset.

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | Local ID; FK target across the canonical schema. |
| `pm_jurisdiction_id` | ULID nullable | Populated when the sidecar syncs from PM (`AUTO_ATTACHED` or `NEW` disposition). Null when locally minted pre-sync (pending sidecar push). |
| `slug` | text(128) NOT NULL UNIQUE | Matches PM's slug. |
| `name` | text NOT NULL | |
| `type_id` | ULID NOT NULL FK | → `clearinghouse_core.jurisdiction_types`. Mirrors PM's FK pattern. Replaces the existing `JurisdictionLevel` StrEnum (4 values → ~16 values via lookup). |
| `valid_from` / `valid_until` | timestamptz nullable | Mirrored from PM. **Names match PM's convention** (was `valid_start`/`valid_end` in original spec). |
| `recorded_at` | timestamptz NOT NULL | Mirrored from PM's clock (preserves PM's view of when the row was active). **Was `transaction_start`.** |
| `superseded_at` | timestamptz nullable | **Was `transaction_end`.** |
| `created_at` / `updated_at` | timestamptz | Local-DB timestamps via `TimestampMixin`. Different from `recorded_at`/`superseded_at` — these are usa-wa's local-cache write times. |

Plus mirror tables `clearinghouse_core.jurisdiction_relationships` and `clearinghouse_core.jurisdiction_relationship_types` (with `is_symmetric` column, 11-code initial vocab) — same shape as their PM counterparts, with `pm_*_id` nullable columns pointing back to the PM-side rows.

**`clearinghouse_core.jurisdiction_identifiers` is dropped from MVP.** PM's design-review decision was to reuse the existing `identifiers` table on the PM side. For usa-wa MVP, local identifier caching is deferred — identifiers are resolved via PM API when needed (the sidecar follow-up plan can add local identifier caching if performance demands). The Role.jurisdiction_id refactor doesn't require identifier resolution at write time; it needs only slug → ULID lookup.

### Schema-wide refactor

Every `jurisdiction_id: text(32)` column across the canonical schema flips to `ULID NOT NULL FK` to `clearinghouse_core.jurisdictions.id`. The natural-key UNIQUE constraints `(jurisdiction_id, source, source_id)` remain — uniqueness shifts from text comparison to FK comparison.

**Tables affected (~30+):**

- `clearinghouse_core.document_identifiers`
- `canonical.persons` / `organizations` / `roles` / `assignments` / `person_identifiers` / `organization_identifiers`
- `canonical.legislative_sessions`
- `canonical.bills` / `bill_types` / `bill_sponsorships` / `bill_actions` / `bill_action_classifications` / `bill_versions` / `amendments` / `bill_titles` / `bill_subjects` / `bill_relationships` / `bill_relationship_types` / `bill_events` / `bill_supplements` / `bill_version_links` / `bill_statutory_citations`
- `canonical.vote_events` / `vote_counts` / `person_votes`
- `canonical.lobbying_activities` / `lobbying_positions` / `contributions`
- `canonical.statute_codes` / `statute_titles` / `statute_chapters` / `statute_sections` / `bill_statute_changes`

### Role refactor (specifically)

- **Drop** `Role.district: text(32)` entirely.
- `Role.jurisdiction_id` (now FK) carries the district reference directly:
  - For "Senator, LD 21" — `Role(name='Senator', jurisdiction_id=<usa-wa-ld-21 cache row>, organization_id=<WA Senate Org>)`
  - For "Chair, Senate Health Committee" — `Role(name='Chair', jurisdiction_id=<usa-wa cache row>, organization_id=<Senate Health Committee Org>)`
- State-level context for a district-specific Role is recoverable via the relationship graph: `usa-wa-ld-21 IS_FULLY_CONTAINED_BY usa-wa`.

**Convention for at-large / leadership / unicameral roles:** `Role.jurisdiction_id` points at the broadest containing jurisdiction the role operates within. For federal "Member of Congress" → `usa`. For WA "Speaker of the House" → `usa-wa`.

### Sidecar (`usa-wa-sync-powermap-jurisdictions.service` or folded into the identity sidecar)

Mirrors the identity producer/archival pattern.

**Read flow (P1+, can ship as soon as PM's read endpoints land):**
1. On adapter init or hourly cron, sidecar pulls PM Jurisdictions for usa-wa scope (`usa` + `usa-wa` + all `usa-wa-*` jurisdictions) via the `GET /jurisdictions` endpoint with `slug_prefix=usa-wa` filter.
2. Sidecar upserts into `clearinghouse_core.jurisdictions` + relationships + identifiers.
3. Bitemporal sync: when PM marks a jurisdiction `valid_end`, the sidecar updates the cache. Existing usa-wa FK references continue pointing at the older cached row — the bitemporal columns answer "what was active on date X" queries via valid_at filters.

**Write flow (P3+, blocked on PM #162 + #164):**
1. WSL adapter encounters a jurisdiction reference not in cache (e.g., post-2030-redistricting LD-21 boundaries change → a new jurisdiction slug).
2. Adapter inserts row with a synthetic local cache placeholder (`pm_jurisdiction_id = NULL`).
3. Sidecar dequeues placeholders and `POST /api/v1/observations` with the jurisdiction payload (see Section 3).
4. PM responds with disposition:
   - `auto-attached` — write returned `pm_jurisdiction_id` to the cache row.
   - `new` — same (PM minted a new row, returned its ID).
   - `rejected` — log structured error, operator notified, row stays unresolved.

### Adapter bootstrap pre-seed

To avoid chicken-and-egg on first deploy and to let usa-wa ship before PM's read endpoints land, the `usa-wa-adapter-legislature` package ships an `initial_jurisdictions.json` covering the WA-relevant set.

**Pre-seed scope (proposed; subject to user review before the JSON file is created — see Open Question 7):**

- `usa` — country.
- `usa-wa` — state.
- **49 state legislative districts** — `usa-wa-ld-1` through `usa-wa-ld-49`.
- **10 congressional districts** — `usa-wa-cd-1` through `usa-wa-cd-10`.
- **All 39 WA counties** (alphabetical, with the underscore-substitution rule applied to multi-word names):

  `usa-wa-county-adams`, `usa-wa-county-asotin`, `usa-wa-county-benton`, `usa-wa-county-chelan`, `usa-wa-county-clallam`, `usa-wa-county-clark`, `usa-wa-county-columbia`, `usa-wa-county-cowlitz`, `usa-wa-county-douglas`, `usa-wa-county-ferry`, `usa-wa-county-franklin`, `usa-wa-county-garfield`, `usa-wa-county-grant`, `usa-wa-county-grays_harbor`, `usa-wa-county-island`, `usa-wa-county-jefferson`, `usa-wa-county-king`, `usa-wa-county-kitsap`, `usa-wa-county-kittitas`, `usa-wa-county-klickitat`, `usa-wa-county-lewis`, `usa-wa-county-lincoln`, `usa-wa-county-mason`, `usa-wa-county-okanogan`, `usa-wa-county-pacific`, `usa-wa-county-pend_oreille`, `usa-wa-county-pierce`, `usa-wa-county-san_juan`, `usa-wa-county-skagit`, `usa-wa-county-skamania`, `usa-wa-county-snohomish`, `usa-wa-county-spokane`, `usa-wa-county-stevens`, `usa-wa-county-thurston`, `usa-wa-county-wahkiakum`, `usa-wa-county-walla_walla`, `usa-wa-county-whatcom`, `usa-wa-county-whitman`, `usa-wa-county-yakima`.

- **Cities:** `usa-wa-city-seattle` only at MVP — WSL adapter signals reference Seattle for some committee-hearing locations. Additional cities (Tacoma, Spokane, Olympia, etc.) added as they appear in adapter data.

**LD type choice (`legislative_district` vs `legislative_district_upper` / `_lower`).** WA's 49 LDs have a shared boundary between Senate (1 Senator) and House (2 Representatives) — one geographic boundary, two chambers' worth of seats. The pre-seed uses the generic `legislative_district` type to model this. Jurisdictions where Senate and House boundaries are independent (most non-WA states) use `legislative_district_upper` / `legislative_district_lower` to distinguish chambers.

**Relationships pre-seeded** (the graph is more important than the entity list):

- `usa-wa IS_FULLY_CONTAINED_BY usa`
- Each LD and CD: `IS_FULLY_CONTAINED_BY usa-wa`
- Each county: `IS_FULLY_CONTAINED_BY usa-wa`
- `usa-wa-city-seattle IS_FULLY_CONTAINED_BY usa-wa` — the state-level containment. Seattle is also seeded with `IS_FULLY_CONTAINED_BY usa-wa-county-king` as a worked example of the city-in-county graph relation; future cities follow the same pattern (state-level containment always; county-level containment when the city sits in one). For cities that span multiple counties (e.g., the Aurora-IL case), the same shape emits multiple `IS_FULLY_CONTAINED_BY` relations.

**File creation completed 2026-06-01.** The file is at `packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/data/initial_jurisdictions.json`. See Open Question 7 for the full tally.

**PM-side ingestion path:** PM's design review decided the bootstrap goes through a **PM-owned admin import** rather than the observation flow. We hand off `initial_jurisdictions.json` to PM as the canonical seed file; PM admin-imports once; subsequent usa-wa observations route against anchored IDs (`AUTO_ATTACHED` from day one). This avoids the awkwardness of bootstrapping through observations (everything would be `NEW`, PM doesn't hold canonical ownership).

Sidecar push to PM is idempotent on slug; first run pushes the bootstrap set. Once PM has the entries (disposition `auto-attached` for re-runs), sync is steady-state.

## Section 3 — PM API contract

REST endpoints aligned with PM's existing pattern (`/api/v1/` prefix, API-key auth via `Authorization: Bearer <key>`, JSON throughout, HTTPS).

### Read endpoints (shipped 2026-06-01 in PM #168 Phase 1)

```
GET  /api/v1/jurisdictions
       ?type=<slug>
       &include_archived=<bool>     # PM convention (consistency with orgs/people endpoints)
       &valid_at=<iso8601>          # bitemporal filter
       &cursor=<opaque>             # cursor-based pagination
       &limit=100

GET  /api/v1/jurisdictions/{id_or_slug}
       # detail by ULID or slug; ETag caching
       ?include=relationships
       &valid_at=<iso8601>

GET  /api/v1/jurisdictions/resolve
       ?slug=<str>                  # OR
       ?scheme=<slug>&value=<str>   # identifier-based lookup
       # fast path — single jurisdiction by slug or external identifier

GET  /api/v1/jurisdictions/{id}/relationships
       ?direction=subject|object|both
       &category=<spatial|governance|functional|temporal>
       &rel_type=<code>
       &valid_at=<iso8601>

GET  /api/v1/jurisdictions/{id}/lineage
       # recursive traversal (depth cap 50)
       # `supersedes` / `succeeded_by` / `evolved_from` edges
```

All routes are read-only behind `X-API-Key`. PM's test coverage is 38 tests green (30 integration + 8 auth).

### Write endpoint (shipped 2026-06-01 in PM #168 Phase 2)

PM established a **per-entity observation endpoint pattern** rather than overloading the shared `/observations` route — `POST /api/v1/jurisdictions/observations` is the jurisdiction-specific entry point. Follow-on PM issue #169 will decompose the existing shared `/observations` into per-entity routes for people / organizations, establishing this as the cohort-wide convention.

```
POST /api/v1/jurisdictions/observations
{
  "source": "usa_wa",
  "source_record_id": "<usa-wa local cache row id>",
  "confidence": 1.0,
  "payload": {
    "slug": "usa-wa-ld-21",
    "name": "Washington State Legislative District 21",
    "type": "legislative_district",
    "valid_from": "2022-01-01T00:00:00Z",
    "identifiers": [
      {"scheme": "jur_ocd", "value": "ocd-division/country:us/state:wa/sldu:21"},
      {"scheme": "jur_wsl_district_id", "value": "21"}
    ],
    "relationships": [
      {"object_slug": "usa-wa", "relationship_type": "is_fully_contained_by"}
    ]
  }
}
```

**Disposition semantics (per PM #168 Phase 2):**

- **`NEW`** — observation creates a jurisdiction row with slug + name + type (all three required for new identifiers; PM `_create_entity` handles the jurisdiction branch).
- **`AUTO_ATTACHED`** — re-observing a known identifier attaches links / contacts / addresses / additional identifiers without requiring core fields.
- **`REJECTED`** — unknown identifier type, missing required fields on `NEW`, invalid type slug, slug uniqueness collision.

PM's `ChangeItem.entity_type` `Literal` widened to include `'jurisdiction'`; `resolve_entity` extended with `create_data` kwarg + `UniqueViolationError` guard; `write_names` no-ops for jurisdictions. PM test coverage: 17 integration tests (15 behaviour + 2 auth), all green.

### Response shape

```json
{
  "id": "01J...",
  "slug": "usa-wa-ld-21",
  "name": "Washington State Legislative District 21",
  "type": "legislative_district",
  "valid_from": "2022-01-01T00:00:00Z",
  "valid_until": null,
  "recorded_at": "2022-09-15T12:00:00Z",
  "superseded_at": null,
  "relationships": [...],
  "identifiers": [...]
}
```

### Sidecar disposition handling

| Disposition | Sidecar action |
|---|---|
| `AUTO_ATTACHED` | Write returned `pm_jurisdiction_id` to local cache row; clear work queue entry. |
| `NEW` | Same — `pm_jurisdiction_id` populated for the first time. |
| `REJECTED` | Structured error log; operator notified; row stays unresolved until manual PM admin action. |

### Error handling

| Scenario | Behavior |
|---|---|
| PM unreachable | Sidecar queues writes locally; reads serve from cache; logs warning. Adapter ingestion continues. |
| Disposition mismatch (PM has a newer bitemporal version) | Sidecar refreshes the cache; existing usa-wa FKs continue pointing at the older cached row (valid_at semantics handle this). |
| Local cache miss during write (jurisdiction not seeded) | Adapter inserts with synthetic placeholder cache row; sidecar pushes via observation; replaces placeholder on disposition response. |

### Testing strategy

- **Unit tests** on cache models + sidecar push/pull logic (no PM dependency).
- **Mock PM with `respx`** for sidecar adapter tests — exercise the observation-shaped POST + the three disposition responses.
- **Integration tests** against the test DB cover the cache + FK refactor.
- **E2E with real PM** deferred until PM ships the endpoints — captured as a P2+ task.

## Section 4 — Migration sequencing + PM coordination

### Migration sequencing in usa-wa

The schema-wide `jurisdiction_id: text(32)` → `ULID FK` refactor is a clean drop-and-rebuild because usa-wa has no production data yet (P0.5 hasn't started ingestion). Single migration:

1. **Pre-seed cache.** Create `clearinghouse_core.jurisdictions` (extended shape — `type_id` FK), `clearinghouse_core.jurisdiction_types` lookup (16 rows seeded to mirror PM), `clearinghouse_core.jurisdiction_relationships` (with `valid_from`/`valid_until`/`recorded_at`/`superseded_at`), `clearinghouse_core.jurisdiction_relationship_types` lookup (11 codes — `exercises_concurrent_jurisdiction` dropped per PM Phase 1; `is_symmetric` column). Seed `clearinghouse_core.jurisdictions` from a bundled `initial_jurisdictions.json` (WA-relevant set).
2. **Add new FK columns.** Every canonical table gains `jurisdiction_id_new: ULID nullable FK`. Same migration.
3. **Backfill.** Single SQL update per table: `UPDATE ... SET jurisdiction_id_new = (SELECT id FROM clearinghouse_core.jurisdictions WHERE slug = 'usa-wa')`. Since `usa-wa` is the only slug today, this is trivial.
4. **Drop old + swap.** Drop `jurisdiction_id_old` (text), rename `jurisdiction_id_new` → `jurisdiction_id`, mark NOT NULL. Reseat natural-key UNIQUE constraints.
5. **Drop `Role.district`** column.

Single migration file, single transaction. Pre-seed runs idempotently via `INSERT ... ON CONFLICT DO NOTHING`. `clearinghouse_core.jurisdiction_identifiers` is **not** part of this migration (deferred to sidecar follow-up).

### PM coordination

Upstream feature request: a new CannObserv/power-map GH issue requesting the Jurisdiction extension.

- **Title:** "Jurisdiction entity type — extend identity model with bounded political/administrative areas"
- **Body:** this sub-spec (sections 1 + 3), with cross-link to the broader research note.
- **Scope cuts:** graph + bitemporal (defer spatial geometry + Cycles/Scenarios), reads + observation-shaped writes (no direct POST), slug convention per Section 1.
- **Dependencies:** consumes PM #162's conflict-resolution semantics, fits inside #164's observation endpoint shape.
- **Sequencing:** read endpoints can ship independently; write path waits on #162 + #164 (same as identity).
- **Asks of PM team:**
  - Confirm slug convention before they lock in URL routes.
  - Confirm vocabulary for `type` enum (open vocab is fine; we want adapters to add new types without PM schema changes).
  - Coordinate on the relationship-type vocabulary — the initial 12-code set in Section 1 is a starting proposal.

The usa-wa-side tracking lives under [usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3) as a sub-task. The cross-repo coordination story: this sub-spec is the contract; both sides build to it; the PM issue links back.

## Open questions for implementation phase

Most original open questions were resolved by PM #168 Phase 1 + Phase 2 (2026-06-01); status updates inline below:

1. ✅ **`clearinghouse_core.Jurisdiction.level` → `type` vocab widening.** Resolved — PM shipped `jurisdiction_types` as a FK lookup with 16 rows. Local cache mirrors: `clearinghouse_core.jurisdiction_types` seeded from PM. Migration drops the old `JurisdictionLevel` StrEnum, replaces with FK to the lookup.
2. ✅ **Bitemporal column naming.** Resolved — PM adopted `valid_from` / `valid_until` / `recorded_at` / `superseded_at`. Local cache mirrors these names. usa-wa's `created_at` / `updated_at` from `TimestampMixin` carries local-DB write times (separate axis).
3. **`Role.jurisdiction_id` semantics for spanning roles.** At-large / leadership / unicameral roles get the broadest containing jurisdiction; district-specific roles get the district. Document in the Role docstring. *(Open — local schema convention.)*
4. **Cycles + Scenarios deferral details.** Bitemporal + lineage relationships support "what was active on date X" queries. Explicit Cycle/Scenario containers deferred until draft-map comparison becomes a query target. *(Open — deferred from MVP.)*
5. ✅ **Naming gap with PM's existing `wa` identifier prefix.** Resolved — PM accepted the `usa-` prefix for jurisdiction slugs as intentionally distinct from the `wa_legislature_member_id`-style identifier slugs. Two parallel conventions confirmed as the right shape (different namespaces).
6. ✅ **Existing `2026-05-27-power-map-integration.md` cleanup.** Resolved 2026-06-02 — sub-spec updated to use the 3-value uppercase disposition vocab (`AUTO_ATTACHED` / `NEW` / `REJECTED`); `queued-for-review` mentions kept only as removal-context annotations. Plan step 7.
7. ✅ **`initial_jurisdictions.json` review gate.** Resolved 2026-06-01 — file at `packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/data/initial_jurisdictions.json`. Final tally: **101 jurisdictions** + **101 relationships** (Seattle additionally `is_fully_contained_by usa-wa-county-king` as a worked example). LDs use generic `legislative_district` type. PM ingests this as a one-time admin import, not via the observation flow.
8. ✅ **`pm.jurisdiction_identifiers` separate table or extend `identifiers`?** Resolved — PM extended the existing polymorphic `identifiers` table (entity_type CHECK on 7 tables; `jur_ocd` / `jur_fips` / `jur_iso3166_2` seeded). usa-wa drops the planned `clearinghouse_core.jurisdiction_identifiers` table from this scope; local identifier caching deferred to sidecar follow-up.
9. ✅ **`exercises_concurrent_jurisdiction` complexity.** Resolved — PM dropped from MVP (LEA-specific, no current cohort need). Slot reserved for future addition.
10. ✅ **Read endpoint sequencing.** Resolved — PM Phase 1 shipped read endpoints independently of #162/#164; Phase 2 then shipped the write endpoint. Sidecar (both flows) is no longer blocked.
11. ✅ **Pre-seed coordination path.** Resolved — PM accepts `initial_jurisdictions.json` as a one-time admin import; usa-wa observation flow operates against anchored IDs from day one.
12. ✅ **Write endpoint shape — shared `/observations` vs per-entity.** Resolved — PM chose per-entity (`POST /api/v1/jurisdictions/observations`), establishing the pattern for the follow-on #169 decomposing the shared `/observations` into per-entity routes.

## Cross-references

- **Research note (input):** [`docs/research/2026-05-30-jurisdictional-information-architecture.md`](../research/2026-05-30-jurisdictional-information-architecture.md)
- **Existing PM integration sub-spec:** [`docs/specs/2026-05-27-power-map-integration.md`](2026-05-27-power-map-integration.md)
- **Hybrid IA:** [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)
- **PM upstream epic:** [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156)
- **PM upstream issues:**
  - [#168](https://github.com/CannObserv/power-map/issues/168) — Jurisdiction entity type (this design's upstream coordination)
  - [#162](https://github.com/CannObserv/power-map/issues/162) — observation conflict-resolution semantics
  - [#164](https://github.com/CannObserv/power-map/issues/164) — `POST /api/v1/observations` endpoint
- **Memories:**
  - `project_identity_producer_archival` — the broader pattern this extends
  - `project_sidecar_sync_pattern` — sidecar architecture template
  - `feedback_jurisdiction_naming` — `usa-wa` convention origin
