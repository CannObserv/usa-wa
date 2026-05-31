# Jurisdictional IA — Power Map extension + usa-wa consumer integration

- **Date:** 2026-05-31
- **Status:** final (brainstorm-approved design; ready for `writing-plans` handoff)
- **Scope:** PM data-model extension for Jurisdictions (new entity type alongside identity), the usa-wa consumer integration shape, and the schema-wide `jurisdiction_id: text(32)` → `ULID FK` refactor that follows from it.
- **Tracks:** [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3); upstream coordination via [CannObserv/power-map#168](https://github.com/CannObserv/power-map/issues/168) (filed 2026-05-31).
- **Inputs:**
  - External exploration: [`docs/research/2026-05-30-jurisdictional-information-architecture.md`](../research/2026-05-30-jurisdictional-information-architecture.md)
  - Existing PM integration: [`docs/specs/2026-05-27-power-map-integration.md`](2026-05-27-power-map-integration.md)
  - Hybrid IA: [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)
  - PM upstream sub-issues: [#162](https://github.com/CannObserv/power-map/issues/162) (observation conflict-resolution), [#164](https://github.com/CannObserv/power-map/issues/164) (`POST /observations`)

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
| Write API shape | Observation pattern (`POST /api/v1/observations` per PM [#164](https://github.com/CannObserv/power-map/issues/164), gated on [#162](https://github.com/CannObserv/power-map/issues/162)) — NOT direct `POST /jurisdictions` |
| Disposition vocabulary | `auto-attached` / `new` / `rejected` (PM has discarded `queued-for-review`) |

## Section 1 — Power Map data model extension

PM adds four new tables in its existing identity schema.

### `pm.jurisdictions` (the entity)

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `slug` | text, UNIQUE | Pattern: `<iso-3166-1-alpha3>-<iso-3166-2>[-<key>-<value>]*`. See "Slug convention" below for formatting rules. Examples: `usa`, `usa-wa`, `usa-wa-ld-21`, `usa-wa-county-king`, `usa-wa-city-seattle`. |
| `name` | text NOT NULL | Display name. |
| `type` | text(32) NOT NULL | Open vocabulary (free-text + documented values, no DB CHECK so adapters stay flexible). Common values: `country` / `state` / `county` / `city` / `legislative_district_upper` / `legislative_district_lower` / `congressional_district` / `judicial_district` / `school_district` / `water_district` / `tribal_nation` / `federal_enclave` / `census_block` / `census_tract` / `other`. |
| `valid_start` | timestamptz nullable | When the jurisdiction is legally active in the real world. |
| `valid_end` | timestamptz nullable | Null = currently active. |
| `transaction_start` | timestamptz NOT NULL | When the record was added to PM (audit + "undo" semantics). |
| `transaction_end` | timestamptz nullable | Null = current row; non-null = superseded by a later transaction. |

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

### `pm.jurisdiction_relationships` (the graph)

The Component/Tags model lives here.

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `subject_jurisdiction_id` | ULID FK | |
| `object_jurisdiction_id` | ULID FK | |
| `relationship_type_id` | ULID FK | → `pm.jurisdiction_relationship_types` |
| `metadata` | jsonb | Weight percentage, basis, legal reference (URL/statute), etc. |
| `valid_start` / `valid_end` | timestamptz | Real-world validity. |
| `transaction_start` / `transaction_end` | timestamptz | PM-record validity. |

**Natural-key UNIQUE:** `(subject_jurisdiction_id, object_jurisdiction_id, relationship_type_id, valid_start)`.

### `pm.jurisdiction_relationship_types` (the lookup)

Codes from the external exploration, plus the `symmetric` flag pattern from `canonical.bill_relationship_types` (hybrid IA v1.3).

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `code` | text(64) UNIQUE | See vocab below. |
| `display_name` | text(128) NOT NULL | |
| `category` | text(16) NOT NULL | `spatial` / `governance` / `functional` / `temporal` — for query filtering. |
| `symmetric` | bool NOT NULL default false | True for `partially_overlaps`, `is_coterminous_with`; false for directed relations like `is_fully_contained_by`. |
| `description` | text nullable | |

**Initial vocabulary:**

| Code | Category | Symmetric |
|---|---|---|
| `is_fully_contained_by` | spatial | no |
| `partially_overlaps` | spatial | yes |
| `is_coterminous_with` | spatial | yes |
| `has_regulatory_authority_over` | governance | no |
| `exercises_concurrent_jurisdiction` | governance | yes |
| `has_extraterritorial_jurisdiction_over` | governance | no |
| `member_of` | functional | no |
| `reports_to` | functional | no |
| `contracts_services_from` | functional | no |
| `supersedes` | temporal | no |
| `succeeded_by` | temporal | no |
| `evolved_from` | temporal | no |

### `pm.jurisdiction_identifiers` (the polymorphic-IDs side table)

Same shape as `pm.person_identifiers`. 1:N child of jurisdiction.

| Column | Type | Notes |
|---|---|---|
| `id` | ULID PK | |
| `jurisdiction_id` | ULID FK | |
| `scheme` | text(64) NOT NULL | Slug per source: `ocd` / `iso_3166_2` / `census_fips` / `geoid` / `wsl_district_id` / `census_geoid_2020` / etc. |
| `value` | text(256) NOT NULL | The identifier value in the scheme's natural format. |
| `verified_at` | timestamptz nullable | |

**Natural-key UNIQUE:** `(jurisdiction_id, scheme)` — one value per scheme per jurisdiction. Plus `(scheme, value)` for cross-jurisdiction lookups.

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
| `pm_jurisdiction_id` | ULID nullable | Populated when the sidecar syncs from PM (auto-attached or new disposition). Null when locally minted pre-sync (pending sidecar push). |
| `slug` | text(128) NOT NULL UNIQUE | Matches PM's slug. |
| `name` | text NOT NULL | |
| `type` | text(32) NOT NULL | Vocabulary per PM Section 1. Replaces the existing `JurisdictionLevel` StrEnum (4 values → ~16 values, free-text). |
| `valid_start` / `valid_end` | timestamptz nullable | Mirrored from PM. |
| `transaction_start` | timestamptz NOT NULL | Mirrored from PM's clock (preserves PM's view of when the row was active). |
| `transaction_end` | timestamptz nullable | |
| `created_at` / `updated_at` | timestamptz | Local-DB timestamps via `TimestampMixin`. Different from `transaction_start/end` — these are usa-wa's local-cache write times. |

Plus mirror tables `clearinghouse_core.jurisdiction_relationships`, `clearinghouse_core.jurisdiction_relationship_types`, `clearinghouse_core.jurisdiction_identifiers` — same shape as their PM counterparts, with `pm_*_id` nullable columns pointing back to the PM-side rows.

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

**Relationships pre-seeded** (the graph is more important than the entity list):

- `usa-wa IS_FULLY_CONTAINED_BY usa`
- Each LD and CD: `IS_FULLY_CONTAINED_BY usa-wa`
- Each county: `IS_FULLY_CONTAINED_BY usa-wa`
- `usa-wa-city-seattle IS_FULLY_CONTAINED_BY usa-wa` (not `IS_FULLY_CONTAINED_BY usa-wa-county-king` — Seattle is contained in King County in practice, but the schema doesn't assume single-county containment; LD/CD overlap with counties is real and we want the same flexibility for cities).

**File creation is gated on user review.** Before the JSON file is created and bundled into the adapter package, the user will see the proposed entity list + relationships and confirm naming conventions align. The spec's pre-seed scope is the proposal; the file is the implementation artifact.

Sidecar push to PM is idempotent on slug; first run pushes the bootstrap set. Once PM has the entries (disposition `auto-attached` for re-runs), sync is steady-state.

## Section 3 — PM API contract

REST endpoints aligned with PM's existing pattern (`/api/v1/` prefix, API-key auth via `Authorization: Bearer <key>`, JSON throughout, HTTPS).

### Read endpoints (direct — no observation wrapper needed)

```
GET  /api/v1/jurisdictions
       ?type=<vocab>
       &slug_prefix=<str>
       &parent_slug=<str>          # direct children of this jurisdiction
       &valid_at=<iso8601>          # bitemporal filter
       &include=relationships,identifiers
       &cursor=<opaque>             # cursor-based pagination
       &limit=100

GET  /api/v1/jurisdictions/{id_or_slug}
       ?include=relationships,identifiers,lineage
       &valid_at=<iso8601>

GET  /api/v1/jurisdictions/{id}/relationships
       ?relationship_type=<code>
       &direction=subject|object|both
       &valid_at=<iso8601>

GET  /api/v1/jurisdictions/{id}/lineage
       # supersedes / succeeded_by / evolved_from graph

GET  /api/v1/jurisdictions/resolve?slug=<str>
       # fast path — slug → Jurisdiction
```

### Write endpoint (observation-pattern aligned per PM #164)

All writes go through the same `POST /api/v1/observations` PM is building for identity. Adds `entity_type: "jurisdiction"` and the jurisdiction payload shape:

```
POST /api/v1/observations
{
  "entity_type": "jurisdiction",
  "source": "usa_wa",
  "source_record_id": "<usa-wa local cache row id>",
  "confidence": 1.0,
  "payload": {
    "slug": "usa-wa-ld-21",
    "name": "Washington State Legislative District 21",
    "type": "legislative_district_upper",
    "valid_start": "2022-01-01T00:00:00Z",
    "identifiers": [
      {"scheme": "ocd", "value": "ocd-division/country:us/state:wa/sldu:21"},
      {"scheme": "wsl_district_id", "value": "21"}
    ],
    "relationships": [
      {"object_slug": "usa-wa", "relationship_type": "is_fully_contained_by"}
    ]
  }
}
```

### Response shape

```json
{
  "id": "01J...",
  "slug": "usa-wa-ld-21",
  "name": "Washington State Legislative District 21",
  "type": "legislative_district_upper",
  "valid_start": "2022-01-01T00:00:00Z",
  "valid_end": null,
  "transaction_start": "2022-09-15T12:00:00Z",
  "transaction_end": null,
  "relationships": [...],
  "identifiers": [...]
}
```

### Disposition handling

Per PM #162, three dispositions:

- **`auto-attached`** — exact match on slug or strong identifier; PM returns the existing `pm_jurisdiction_id`.
- **`new`** — no match; PM creates the row and returns the new id.
- **`rejected`** — payload validation failure, ambiguous resolution PM can't safely auto-decide, or trust-model failure.

Sidecar handling:

| Disposition | Sidecar action |
|---|---|
| `auto-attached` | Write returned `pm_jurisdiction_id` to local cache row; clear work queue entry. |
| `new` | Same — `pm_jurisdiction_id` populated for the first time. |
| `rejected` | Structured error log; operator notified; row stays unresolved until manual PM admin action. |

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

1. **Pre-seed cache.** Create `clearinghouse_core.jurisdictions` (extended shape) + relationships + identifiers + relationship_types lookup. Seed `clearinghouse_core.jurisdiction_relationship_types` with the full vocab (12 codes). Seed `clearinghouse_core.jurisdictions` from a bundled `initial_jurisdictions.json` (WA-relevant set).
2. **Add new FK columns.** Every canonical table gains `jurisdiction_id_new: ULID nullable FK`. Same migration.
3. **Backfill.** Single SQL update per table: `UPDATE ... SET jurisdiction_id_new = (SELECT id FROM clearinghouse_core.jurisdictions WHERE slug = 'usa-wa')`. Since `usa-wa` is the only slug today, this is trivial.
4. **Drop old + swap.** Drop `jurisdiction_id_old` (text), rename `jurisdiction_id_new` → `jurisdiction_id`, mark NOT NULL. Reseat natural-key UNIQUE constraints.
5. **Drop `Role.district`** column.

Single migration file, single transaction. Pre-seed runs idempotently via `INSERT ... ON CONFLICT DO NOTHING`.

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

Captured as design notes — not blocking the spec, but worth resolving during `writing-plans`:

1. **`clearinghouse_core.Jurisdiction.level` → `type` vocab widening.** Existing `JurisdictionLevel` StrEnum has 4 values. New `type` vocab is ~16 values (open). Migration drops the enum, replaces with `text(32)`. Vocabulary documented in column docstring; no DB CHECK so adapters stay flexible.
2. **Bitemporal `transaction_*` clock source.** Recommendation: PM's clock for the cache `transaction_start` / `transaction_end` (preserves PM's view of when a row was active); usa-wa's `created_at` / `updated_at` from `TimestampMixin` carries the local-DB write times. Two clocks, distinct semantics — both useful.
3. **`Role.jurisdiction_id` semantics for spanning roles.** At-large / leadership / unicameral roles get the broadest containing jurisdiction; district-specific roles get the district. Document in the Role docstring.
4. **Cycles + Scenarios deferral details.** Bitemporal + the lineage relationship types support "what was active on date X" queries. Explicit Cycle/Scenario containers come when draft-map comparison becomes a query target. Not in MVP; not in P1 either.
5. **Naming gap with PM's existing `wa` identifier prefix.** PM's existing identifier slugs use `wa`; the new Jurisdiction slugs use `usa-wa`. Two parallel conventions in PM. Flag to PM maintainer — may want to align both eventually, or accept that identifier slugs vs jurisdiction slugs are different concepts.
6. **Existing `2026-05-27-power-map-integration.md` cleanup.** Sub-spec still references the discarded `queued-for-review` disposition. Worth a separate cleanup pass.
7. **`initial_jurisdictions.json` review gate.** Per user direction, the proposed pre-seed entity list (39 counties + 49 LDs + 10 CDs + Seattle + state + country = 100 entries) and the relationship graph are reviewed before the JSON file is created. Naming conventions checked against the slug rules in Section 1. Resolution happens during writing-plans Step 1 (pre-seed file creation).

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
