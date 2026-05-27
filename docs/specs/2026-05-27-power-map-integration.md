# Power-map integration

- **Date:** 2026-05-27
- **Status:** final (deliverable 4 of P0.5; refreshed 2026-05-28 against [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156) post-triage)
- **Scope:** How usa-wa integrates with [CannObserv/power-map](https://github.com/CannObserv/power-map) — read flow, deferred write flow, sidecar sync architecture, and the staging strategy across phases.
- **Tracks:** [GH #3](https://github.com/CannObserv/usa-wa/issues/3); upstream epic [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156).
- **Inputs:** [`docs/research/2026-05-26-power-map-integration-contract.md`](../research/2026-05-26-power-map-integration-contract.md), [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md), project memory [[project-identity-producer-archival]] and [[project-sidecar-sync-pattern]].

## Problem

usa-wa's hybrid IA v1 adopts power-map's identity vocabulary (Person / Organization / Role / Assignment). The integration model needs to be explicit: where canonical-identity records originate, where they live long-term, what's currently mechanically possible against the published power-map surface, what's gated on upstream feature work, and how synchronization works architecturally. Without that explicitness, every adapter-author re-derives the rules each time they touch a `powermap_*_id` column.

## Producer / archival framing (reminder)

usa-wa is **both** a query layer over primary WA sources **and** a producer of canonical-identity records archived in power-map. The framing rests on two observations:

1. **State-resource access is unreliable.** WSL SOAP rate-limits, breaks compatibility, and rotates IDs over time.
2. **Canonical identity is cross-cohort.** power-map already serves observo, archiver, and other CannObserv siblings. Re-implementing identity in usa-wa would fragment the cohort.

So: usa-wa runs the adapter that translates WSL data into Person / Organization / Role / Assignment records (it's the cohort member with the data); the records live in usa-wa's local Postgres for query latency; the long-term truth lives in power-map. When the WSL outage of 2027 happens, usa-wa keeps serving from the local cache while the canonical-identity archive in power-map remains the authoritative reference for cross-cohort consumers.

Captured in project memory [[project-identity-producer-archival]].

## Architectural mechanism: sidecar sync

Sync between usa-wa's local canonical tables and power-map is **out-of-band**, via dedicated sidecar processes — not in-line with MCP/REST request handling. The pattern is generalizable across the cohort:

- `usa-wa-sync-powermap` watches `canonical.persons` / `canonical.organizations` / `canonical.person_identifiers` / `canonical.organization_identifiers` / `canonical.assignments` for inserts and updates, then pushes via power-map's observation API ([#164](https://github.com/CannObserv/power-map/issues/164)) once that ships.
- A future `usa-wa-sync-archiver` handles content registration once Replicator is online (deferred indefinitely).
- **Bidirectional sync** when both sides have changed within the local TTL: the sidecar pulls upstream state, reconciles per upstream conflict-resolution rules ([#162](https://github.com/CannObserv/power-map/issues/162)), writes back.
- **Read fan-out** is implemented inside the `AdapterRunner.fetch_and_normalize` path — adapters can opt into consulting power-map and primary source in parallel, or bootstrap from archives first and backfill from primary later.

In-band sync was rejected because it couples service health to upstream availability — a slow power-map would degrade MCP responses, and the responsibility boundary between "serve a query" and "maintain the canonical archive" would blur. Captured in project memory [[project-sidecar-sync-pattern]].

## Upstream state — power-map epic #156

After our initial feature-request landed, power-map's maintainer reframed [#156](https://github.com/CannObserv/power-map/issues/156) as an epic with explicit sub-issues, descoping decisions, and a dependency graph. Refresh as of 2026-05-28:

### Phase 1 — shipped

| Issue | Status | Work |
|---|---|---|
| [#157](https://github.com/CannObserv/power-map/issues/157) | **CLOSED** | Seeded `person_wa_legislature_member_id` and `org_wa_legislature_committee_id` identifier types. |

**Note the naming.** Power-map adopted `person_wa_legislature_member_id` (matching its established `person_wa_pdc` / `org_wa_pdc` slug pattern: `<entity>_<jurisdiction>_<system>_<key>`), **not** the `person_wsl_*` variant from our original request. usa-wa references the canonical power-map slugs everywhere.

### Phase 2a — read surface foundation

| Issue | Status | Work | Notes |
|---|---|---|---|
| [#158](https://github.com/CannObserv/power-map/issues/158) | OPEN | `GET /api/v1/people/search` + `GET /api/v1/people/{id}` | Mirrors orgs pattern; **must** apply `visibility='public'` filter on all `person_names` queries (deadname / privacy compliance). |
| [#161](https://github.com/CannObserv/power-map/issues/161) | OPEN | Auth model docs (scope, rate limits, key lifecycle, pagination) | Docs only; no code. |

### Phase 2b — read surface quality (depends on #158)

| Issue | Status | Work |
|---|---|---|
| [#159](https://github.com/CannObserv/power-map/issues/159) | OPEN | Identifier-filter on `orgs/search` + `people/search` (`?identifier_type=&identifier_value=`). Adds `idx_identifiers_lookup` index. |
| [#160](https://github.com/CannObserv/power-map/issues/160) | OPEN | ETag / Last-Modified / If-None-Match on detail endpoints. |

### P3 — write path (deferred)

| Issue | Status | Work | Notes |
|---|---|---|---|
| [#162](https://github.com/CannObserv/power-map/issues/162) | OPEN | **Design**: conflict-resolution semantics for the observation endpoint. Blocks #164. Decisions on dispositions (auto-attached / new / queued-for-review / rejected), match strategy (exact only vs. fuzzy fallback), confidence threshold, admin queue, trust model. |
| [#163](https://github.com/CannObserv/power-map/issues/163) | OPEN | `GET /api/v1/changes` — entity change feed for sibling-service cache invalidation. |
| [#164](https://github.com/CannObserv/power-map/issues/164) | OPEN | `POST /api/v1/observations` — sibling-service observation/upsert. Hard-blocked on #162. |

### Descoped

| Original ask | Decision | Rationale |
|---|---|---|
| Bulk-resolve endpoint (`POST /api/v1/identifiers/resolve`) | Deferred; reassess after #159 | Identifier-filter on search solves the N+1 problem for backfill. Revisit if per-request overhead is still painful in practice. |
| Python SDK | Declined | API still evolving. Recommendation: generate a typed client via `openapi-python-client` against `/openapi.json`. Revisit after Phase 2b stabilizes. |
| Pagination | Already shipped — documented under #161 | `limit`, `offset`, `has_more` live on all list endpoints. |

### Timing

Per power-map maintainer: **most of Phase 1 + 2a + 2b will ship before usa-wa begins additional work here**. P3 (#162 design → #164 implementation, plus #163) is sequenced later. usa-wa's P2 should not be blocked by upstream timing; P3 may be.

## Today's mechanically available surface

After #157 closed, power-map exposes:

- `GET /api/v1/orgs/search` (with `limit`/`offset`/`has_more` pagination)
- `GET /api/v1/orgs/{id}`
- `X-API-Key` header auth (docs in flight per #161)
- Schema-level: `entity_identifier_types` now seeds `org_wa_pdc`, `person_wa_pdc`, `person_ssn`, **`person_wa_legislature_member_id`**, **`org_wa_legislature_committee_id`**.

Writes still go through the HTMX admin UI or the CSV bulk-import (which dedupes on lowercased legal name, **not** on identifier).

## Read flow (P2 — usa-wa pulls identity FROM power-map)

Concrete integration once Phase 2a + 2b ship:

```text
WSL adapter emits a Filer-as-Organization → Organization.source_id = "L-12345"
                                          → Organization.powermap_organization_id = ???

         ↓ sidecar / on-demand resolver (P2):

GET /api/v1/orgs/search?identifier_type=org_wa_pdc&identifier_value=L-12345
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                       gated on #159 (identifier-filter)

Response: { "results": [{ "id": "01HZW...", "name": "Acme Government Affairs LLC", ... }] }

         ↓

Organization.powermap_organization_id = "01HZW..."  ✓
```

Mapping table:

| usa-wa entity | power-map entity | Column populated | Upstream gates |
|---|---|---|---|
| `Organization` (source=usa_wa_pdc) | Organization | `organizations.powermap_organization_id` | #159 (identifier-filter) |
| `Organization` (source=usa_wa_legislature, org_type=committee) | Organization | `organizations.powermap_organization_id` | #159 |
| `Person` (source=usa_wa_legislature) | Person | `persons.powermap_person_id` | #158 (people endpoints) + #159 (identifier-filter) |
| `Person` (source=usa_wa_pdc) | Person | `persons.powermap_person_id` | #158 + #159 |
| `Role` | Role | (deferred — power-map's role API surface not yet exposed) | TBD post P2b |
| `Assignment` | RoleAssignment | (deferred) | TBD post P2b |

**External-identifier graph already lives locally.** `canonical.person_identifiers` and `canonical.organization_identifiers` (added in hybrid IA v1) capture the N-scheme mapping for each Person and Organization. So even before power-map's read endpoints are everywhere, sibling services querying usa-wa for "the power-map ID for the org behind PDC filer L-12345" can join through usa-wa's own table when `powermap_organization_id` is set. The sidecar populates that column as soon as #159 is live; until then it stays null.

## Write flow (P3+ — usa-wa pushes observations via sidecar)

The `usa-wa-sync-powermap` sidecar process handles writes. Target shape (sketched per #164; finalized once #162 design lands):

```http
POST /api/v1/observations
X-API-Key: ...

{
  "claimed_kind": "person",
  "claimed_identifiers": [
    {"type": "person_wa_legislature_member_id", "value": "26142"}
  ],
  "claimed_attributes": {
    "name_full": "Jane Q. Doe",
    "name_first": "Jane", "name_last": "Doe",
    "primary_role": {
      "organization_identifiers": [{"type": "org_wa_legislature_chamber", "value": "senate"}],
      "role_name": "Senator",
      "district": "21",
      "valid_from": "2023-01-09"
    }
  },
  "observer": "usa-wa@2026-05-27"
}

Response 200:
{
  "matched_entity_id": "01HZW...",
  "disposition": "auto-attached",   # | new | queued-for-review | rejected
  "confidence": 0.97,
  "notes": "Matched on person_wa_legislature_member_id"
}
```

The sidecar writes the returned `matched_entity_id` to `persons.powermap_person_id` and clears its work queue for that row. Observation-on-update is handled the same way — power-map's match strategy (gated on #162) decides whether the update auto-merges, queues for review, or rejects.

## Dependency matrix → usa-wa phases

Maps the 8 power-map sub-issues to usa-wa phase gates.

| #156 sub-issue | Status | Gates usa-wa work |
|---|---|---|
| #157 seeds | shipped | Already unlocks usa-wa's identifier columns referencing `person_wa_legislature_member_id` and `org_wa_legislature_committee_id`. |
| #158 People endpoints | OPEN | P2 blocker — Person resolution (legislator → power-map Person). |
| #161 auth docs | OPEN | P2 blocker — operational knowledge required before usa-wa adapter goes live. |
| #159 identifier-filter | OPEN | P2 blocker — without it, finding Org/Person by external ID requires scanning paginated results. |
| #160 cache headers | OPEN | P2 quality-of-life — refresh-cycle efficiency. |
| #162 conflict-resolution design | OPEN | P3 blocker — sidecar write flow can't safely operate without documented dispositions and merge semantics. |
| #163 change feed | OPEN | P3 quality-of-life — usa-wa sidecar consumes this to invalidate stale `powermap_*_id` mappings when power-map merges/splits. |
| #164 observations endpoint | OPEN, blocked on #162 | P3 blocker — sidecar push target. |

**Phase summary:**

- **P2 ships when:** #157 (done) + #158 + #159 + #161 are live in power-map.
- **P2 ships well when:** #160 also lands.
- **P3 ships when:** #162 design decisions finalized + #164 implemented.
- **P3 ships well when:** #163 also lands.

## Column-shape commitment

Confirmed and unchanged from the v0 → v1 transition:

- **`canonical.persons.powermap_person_id`** stays as a nullable ULID FK column. Null pre-resolution; populated by the sidecar after a successful #159 lookup (P2) or #164 observation response (P3).
- **`canonical.organizations.powermap_organization_id`** same shape.
- **No JSONB `external_ids` bag.** The N-cardinality cross-system graph lives in `canonical.person_identifiers` and `canonical.organization_identifiers`.
- **`powermap` is one valid value for `*_identifiers.scheme`.** The denormalized FK column on Person/Organization is functionally a copy of `(person_id, scheme='powermap', value='<powermap ULID>')` for fast joins.

## Staging strategy (gap-fill until #164 ships)

usa-wa goes live and ingests WA data **before** power-map's write API exists. Without a plan, our local canonical-identity records would have no path to upstream, drifting from the cohort's eventual reality.

The staging plan:

1. **Adapter writes locally first, always.** Every WSL/PDC adapter run produces complete Person / Organization / Role / Assignment records in `canonical.*`. `powermap_*_id` columns stay null until resolved. This is the steady state from P1a through P3.

2. **Local IDs are authoritative for usa-wa's MCP/REST surface.** External consumers of usa-wa get usa-wa's ULIDs in citations and responses. `powermap_*_id` is metadata, not a primary identifier.

3. **Read-time resolution (P2).** When #159 + #158 ship, the `usa-wa-sync-powermap` sidecar sweeps unresolved local entities and populates `powermap_*_id` from the identifier-filter lookup. Local IDs remain stable; we never rewrite primary keys.

4. **Backfill push (P3).** Once #164 lands and #162 design is settled, the sidecar runs a one-shot backfill over every local Person and Organization that still lacks `powermap_*_id` (e.g., entities power-map didn't yet know about). It formats the observation payload, POSTs, writes back the returned ID. Idempotent — re-running against already-resolved entities is a no-op (auto-attached disposition).

5. **Steady-state push (P3+).** After backfill, every adapter run inserts new rows into the sidecar's work queue. The sidecar dequeues and pushes observations asynchronously; the adapter never waits on power-map.

6. **Change-feed consumption (P3+, gated on #163).** When power-map merges, splits, or renames an entity, the change feed informs the sidecar, which updates the corresponding `powermap_*_id` column. Without this, mappings drift silently.

7. **Bootstrap-from-archives (P3+ optimization).** For backfill cases where usa-wa is being initialized from scratch but power-map already has rich data, the sidecar can pull canonical identity from power-map first (faster, broader) before backfilling from primary WSL/PDC. Defer to when the use case is concrete.

## Open questions / risks

1. **#162 design timing.** Until the conflict-resolution semantics are documented, the sidecar's write flow can't be implemented safely — usa-wa wouldn't know whether a name mismatch causes auto-merge, queues for review, or rejects the observation. Track #162; participate in the design conversation.
2. **OpenAPI codegen choice.** Power-map declined to publish an SDK and recommended `openapi-python-client` against `/openapi.json`. Verify the generated client meets our typing needs before committing — fall back to a hand-rolled `httpx` client if codegen output is awkward.
3. **Identifier-type proliferation.** Power-map's slug pattern is `<entity>_<jurisdiction>_<system>_<key>` (per the closed #157: `person_wa_legislature_member_id`, `org_wa_legislature_committee_id`). For future Oregon-sibling work, the pattern extends naturally (`person_or_legislature_member_id`). usa-wa coordinates with power-map maintainer before requesting new seeds; out-of-band schema additions cause integration bugs.
4. **Sidecar process management.** Where does the sidecar run — same systemd unit as the API, a separate one, or a periodic job? P1a operational discussion.
5. **Bidirectional sync conflict surface.** When usa-wa's local cache and power-map both update the same Person row within the local TTL, the sidecar reconciles. Need a deterministic rule: power-map wins (it's the system of record)? Most-recent-update wins? Per-field merge? Per [[project-sidecar-sync-pattern]], upstream rules (#162) define the policy; usa-wa enforces.

## Cross-references

- Power-map research note (input): [`docs/research/2026-05-26-power-map-integration-contract.md`](../research/2026-05-26-power-map-integration-contract.md) (historical; pre-#156 triage)
- Hybrid legislative IA: [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)
- MVP architecture spec: [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md)
- Upstream epic: [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156)
- Tracking issue: [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3)
- P0.5 plan: [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md)
- Project memory: [[project-identity-producer-archival]], [[project-sidecar-sync-pattern]]
