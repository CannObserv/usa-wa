# Power-map integration

- **Date:** 2026-05-27
- **Status:** final (deliverable 4 of P0.5)
- **Scope:** How usa-wa integrates with [CannObserv/power-map](https://github.com/CannObserv/power-map) — read flow for P2, write flow for P3+, and the staging strategy in between.
- **Tracks:** [GH #3](https://github.com/CannObserv/usa-wa/issues/3); upstream feature request [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156).
- **Inputs:** [`docs/research/2026-05-26-power-map-integration-contract.md`](../research/2026-05-26-power-map-integration-contract.md), [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md), [project_identity_producer_archival](../../../.claude/projects/-home-exedev-usa-wa/memory/project_identity_producer_archival.md).

## Problem

usa-wa's hybrid IA v1 adopts power-map's identity vocabulary (Person / Organization / Role / Assignment). The integration model needs to be explicit: where canonical-identity records originate, where they live long-term, what's currently mechanically possible against the published power-map surface, and what's gated on upstream feature work. Without that explicitness, every adapter-author will re-derive the rules each time they touch a `powermap_*_id` column.

## Producer / archival framing (reminder)

usa-wa is **both** a query layer over primary WA sources **and** a producer of canonical-identity records that should ultimately be archived in power-map. The framing rests on two observations:

1. **State-resource access is unreliable** — WSL SOAP rate-limits, breaks compatibility, and rotates IDs over time.
2. **Canonical identity is cross-cohort** — power-map already serves observo, archiver, and other CannObserv siblings. Re-implementing identity in usa-wa would fragment the cohort.

So: usa-wa runs the adapter that translates WSL data into Person / Organization / Role / Assignment records (it's the cohort member with the data); the records live in usa-wa's local Postgres for query latency; and the long-term truth lives in power-map. When the WSL outage of 2027 happens, usa-wa keeps serving from the local cache while the canonical-identity archive in power-map remains the authoritative reference for cross-cohort consumers.

This framing is captured in project memory [[project-identity-producer-archival]].

## Today's surface (as found, 2026-05-25)

Per the [research note](../research/2026-05-26-power-map-integration-contract.md), the publicly-callable power-map surface is narrower than the framing implies:

- **2 public endpoints, both Org-only, both read-only:**
  - `GET /api/v1/orgs/search`
  - `GET /api/v1/orgs/{id}`
- **Auth:** `X-API-Key` header. Scope / rate-limit / rotation expectations not documented.
- **No People endpoints.** No `GET /api/v1/people/*`.
- **No identifier-filter** on search. Search is name-based; finding the Org whose `identifier_value == 'L-12345'` requires scanning.
- **No SDK.** No Python client published.
- **No write API.** Writes go through:
  - the HTMX admin UI (humans)
  - a CSV bulk-import that dedupes on lowercased legal name (**not** on identifier)
- **No change feed / webhooks.** Subscribers can't be notified when an Org merges, splits, or renames.

The **schema** is fully fit-for-purpose: `people` + `organizations` + `roles` + `role_assignments` + `identifiers × entity_identifier_types`. The gap is in the public API surface, not the data model. Seeded identifier-type slugs include `org_wa_pdc`, `person_wa_pdc`, `person_ssn`. **`person_wsl_member_id` and `org_wsl_committee_id` are NOT seeded.**

## Read flow (P2 — usa-wa pulls identity FROM power-map)

What usa-wa needs from power-map at P2: the ability to look up a power-map canonical entity by its WSL or PDC identifier, and pull back the canonical name and ID.

```text
WSL adapter emits a Filer row → Filer.source_id = "L-12345"
                              → Filer.powermap_organization_id = ???

         ↓ usa-wa power-map adapter (P2):

GET /api/v1/orgs/search?identifier_type=org_wa_pdc&identifier_value=L-12345
                                        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                        blocked on power-map#156 §2

Response: { "results": [{ "id": "01HZW...", "name": "Acme Government Affairs LLC", ... }] }

         ↓

Filer.powermap_organization_id = "01HZW..."  ✓
```

Concrete integration:

| usa-wa entity | power-map entity | usa-wa column populated | Upstream gates |
|---|---|---|---|
| `Organization` (org_type=lobbying_firm, source=usa_wa_pdc) | Organization | `organizations.powermap_organization_id` | Identifier-filter on `orgs/search` (#156 §2) |
| `Organization` (org_type=committee, source=usa_wa_legislature) | Organization | `organizations.powermap_organization_id` | #156 §2 + `org_wsl_committee_id` seeded (#156 §3) |
| `Organization` (org_type=candidate_committee, source=usa_wa_pdc) | Organization | `organizations.powermap_organization_id` | #156 §2 |
| `Organization` (org_type=lobbying_firm) | Organization | `organizations.powermap_organization_id` | #156 §2 |
| `Person` (source=usa_wa_legislature) | Person | `persons.powermap_person_id` | People endpoints (#156 §1) + identifier-filter (#156 §2) + `person_wsl_member_id` seeded (#156 §3) |
| `Person` (source=usa_wa_pdc) | Person | `persons.powermap_person_id` | #156 §1 + §2 |
| `Role` | Role | `roles.powermap_role_id` (TBD) | Defer; lower priority than People |
| `Assignment` | RoleAssignment | (TBD) | Defer |

**Person ID resolution slides from P2 to P3.** The research note's recommended P2 scope is read-only thin slice for Organizations only (~2 days of usa-wa work *once* power-map ships the identifier-filter endpoint and seeds the WA identifier types). Person resolution requires entirely new upstream endpoints (#156 §1) that don't exist today — that's a P3 concern.

**External-identifier graph (`canonical.person_identifiers`, `canonical.organization_identifiers`).** usa-wa already stores its own copy of every external-ID-scheme mapping at v1 (per the hybrid IA spec). This means we're not blocked on power-map for cross-cohort identifier resolution — when archiver wants "the power-map ID for the org behind PDC filer L-12345", it can ask usa-wa's `organization_identifiers` table directly. Power-map remains canonical, but our local table doesn't have to round-trip.

## Write flow (P3+ — usa-wa pushes observations TO power-map)

What usa-wa eventually wants: when the WSL adapter encounters a previously-unseen legislator at `member_id=42`, it pushes an *observation* to power-map ("I saw a Person named 'Sen. Jane Doe' with `person_wsl_member_id=42`; here are the attributes; please attach to a known Person or create a new one"). Power-map decides whether to auto-attach, queue for admin review, or reject.

This direction is **mechanically impossible today**. The blocking facts:

- No `POST /api/v1/observations` endpoint or equivalent (gated on #156 §9).
- No documented conflict-resolution semantics (gated on #156 §10).
- No change-feed for usa-wa to receive updates when power-map's canonical-identity decisions change (gated on #156 §11).

Target shape (sketched, not committed — finalized when #156 §9 lands):

```http
POST /api/v1/observations
X-API-Key: ...

{
  "claimed_kind": "person",
  "claimed_identifiers": [
    {"type": "person_wsl_member_id", "value": "42"}
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
  "matched_entity_id": "01HZW...",          # power-map canonical Person ULID
  "disposition": "auto_attached",            # | new | queued_for_review | rejected
  "confidence": 0.97,
  "notes": "Matched on person_wsl_member_id"
}
```

usa-wa's adapter would then write the returned `matched_entity_id` to the corresponding `persons.powermap_person_id` column.

**Conflict resolution.** When usa-wa pushes `name="Jane Doe"` for an identifier and power-map already has `name="Jane Q. Doe"`, what happens? Per #156 §10 — the published rule should be queryable per `entity_identifier_type`. Until that's documented, usa-wa's MVP write flow can't safely run; we don't know whether a name mismatch causes auto-merge, queues for review, or rejects the entire observation. Wait.

## Upstream dependency matrix

Maps the 11 asks in [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156) to usa-wa phase gates. Phases later than P3 are aspirational.

| #156 ask | Tier | Gates which usa-wa work |
|---|---|---|
| §1 People endpoints (`GET /api/v1/people/search`, `GET /api/v1/people/{id}`) | P2 blocker | Person resolution (legislator → power-map Person) |
| §2 Identifier-filter on `orgs/search` and `people/search` | P2 blocker | Both Org and Person resolution. Without this, scan-all-paginated is the only approach and impractical at scale. |
| §3 Seed `person_wsl_member_id`, `org_wsl_committee_id` identifier types | P2 blocker | WSL Person and Committee resolution |
| §4 Documented `X-API-Key` auth model | P2 blocker | We need to know scope / rate / rotation to operationalize the adapter |
| §5 Bulk-resolve endpoint | P2 quality-of-life | Improves initial backfill round-trip cost (~150 legislators → 1 call) |
| §6 Cache headers (`ETag` / `Last-Modified`) | P2 quality-of-life | Improves refresh-cycle efficiency |
| §7 Pagination on search | P2 quality-of-life | Backfill iteration; mandatory if scan-all is needed |
| §8 Minimal Python SDK | P2 quality-of-life | Avoids each sibling rolling their own typed client |
| §9 Observation / upsert endpoint | P3 blocker | usa-wa's write flow (push observations) — the whole point of producer/archival framing |
| §10 Conflict-resolution semantics | P3 blocker | Required before any write flow can safely operate |
| §11 Change feed / webhooks | P3 quality-of-life | Lets usa-wa invalidate cached `powermap_*_id` mappings when power-map merges/splits/renames |

**Phase summary:**

- **P2 ships when:** §1 + §2 + §3 + §4 are live in power-map.
- **P2 ships well when:** §5 + §6 + §7 + §8 also land.
- **P3 ships when:** §9 + §10 are live and documented.
- **P3 ships well when:** §11 also lands.

## Column-shape commitment

Confirmed by the research note and not changed by P0.5 step 1-5 work:

- **`canonical.persons.powermap_person_id`** stays as a nullable ULID FK column. Null pre-resolution; populated after a successful P2 lookup.
- **`canonical.organizations.powermap_organization_id`** same shape.
- **No JSONB `external_ids` bag-of-identifiers column.** The N-cardinality cross-system mapping lives in `canonical.person_identifiers` and `canonical.organization_identifiers` (1:N child tables added in v1).
- **`powermap` is one valid value for `*_identifiers.scheme`.** A Person's `powermap_person_id` column is functionally a denormalized copy of the row `(person_id, scheme='powermap', value='<powermap ULID>')` in `person_identifiers`. The denormalization buys fast-path joins; the child table is the canonical place when N=many.

## Staging strategy (gap-fill until write API ships)

usa-wa goes live and ingests WA data **before** power-map's write API exists. Without a plan, our local canonical-identity records would have no path to upstream, drifting from the cohort's eventual reality.

The staging plan:

1. **Adapter writes locally first, always.** Every WSL/PDC adapter run produces complete Person / Organization / Role / Assignment records in `canonical.*`. `powermap_*_id` columns stay null. This is the steady state from P1a through P3.

2. **Local IDs are authoritative for usa-wa's MCP/REST surface.** External consumers of usa-wa get usa-wa's ULIDs in citations and responses. The `powermap_*_id` column is metadata, not a primary identifier.

3. **Read-time resolution (P2).** When power-map's read endpoints ship, a periodic adapter run sweeps unresolved local entities and populates `powermap_*_id` from the `identifier-filter` lookup. Local IDs remain stable; we never rewrite primary keys.

4. **Backfill push (P3+).** Once the observation endpoint (#156 §9) lands, a one-shot backfill job scans every local Person and Organization that doesn't have a `powermap_*_id`, formats the observation payload, POSTs to power-map, and writes back the returned ID. The job is idempotent — re-running against already-resolved entities is a no-op.

5. **Steady-state push (P3+).** After backfill, every adapter run pushes a new observation when it creates a new local Person / Organization, and updates the local `powermap_*_id` from the response. The "observe-or-create" semantic comes from power-map; usa-wa just forwards.

6. **Change-feed consumption (P4+, gated on #156 §11).** When power-map merges, splits, or renames an entity, the change feed informs usa-wa to update the corresponding `powermap_*_id` column. Without this, mappings drift silently and usa-wa accumulates stale references.

## Open questions / risks

1. **No power-map maintainer ETA on #156.** The 11 asks aren't bucketed by them yet. usa-wa's P2 is on hold until #156 §1–§4 ship in some form.
2. **Role and Assignment integration is sketched, not committed.** This spec defers them ("TBD" in the read-flow table). Power-map has `roles` and `role_assignments` in its schema, but the public surface doesn't expose them. The producer/archival framing implies usa-wa eventually pushes these too. Specify when #156 §9 sketch lands.
3. **Identifier-type proliferation.** Once power-map seeds `person_wsl_member_id`, usa-wa will want `person_usa_wa_pdc_filer_id`, `person_usa_or_legislature_member_id` (for an Oregon sibling), and so on. The pattern is `person_<jurisdiction>_<source>_<identifier>` — confirm with power-map maintainers before usa-wa starts requesting seeds.
4. **What happens to the local `canonical.person_identifiers` row when power-map's canonical Person merges two entities we'd seen as distinct?** Today no path notifies usa-wa. The conflict-resolution semantics in #156 §10 + change-feed in #156 §11 will eventually answer this; until both ship, usa-wa silently retains the pre-merge view.
5. **CSV bulk-import fallback for backfill.** If #156 §9 takes long enough, usa-wa could export canonical-identity records as CSV and feed power-map's existing bulk-import (which dedupes on name, not identifier — risky). Defer; not recommended as the primary path.

## Cross-references

- Power-map research note (input): [`docs/research/2026-05-26-power-map-integration-contract.md`](../research/2026-05-26-power-map-integration-contract.md)
- Hybrid legislative IA (parent): [`docs/specs/2026-05-27-hybrid-legislative-ia.md`](2026-05-27-hybrid-legislative-ia.md)
- MVP architecture spec (parent): [`docs/specs/2026-05-25-usa-wa-mvp-design.md`](2026-05-25-usa-wa-mvp-design.md)
- Upstream feature request: [CannObserv/power-map#156](https://github.com/CannObserv/power-map/issues/156)
- Tracking issue: [CannObserv/usa-wa#3](https://github.com/CannObserv/usa-wa/issues/3)
- P0.5 plan: [`docs/plans/2026-05-26-p0-5-hybrid-legislative-ia.md`](../plans/2026-05-26-p0-5-hybrid-legislative-ia.md)
