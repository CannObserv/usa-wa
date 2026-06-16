# Upstream issue — Power Map re-seed guidance for the usa-wa subtree

**Status:** filed as [CannObserv/power-map#206](https://github.com/CannObserv/power-map/issues/206).
This doc is the retained source of record for that guidance.

---

## Context

Power Map's jurisdiction/identity data was reset around the #203 rollout. The `usa-wa`
deployment has adapted its sidecar to the #203 per-key subscription model (downstream:
CannObserv/usa-wa#10) and run its cutover bootstrap. This issue captures **what PM must hold
for the `usa-wa` subtree** so re-seeding (now and after any future reset) matches what
usa-wa's sync expects — and so usa-wa's own producer observations **AUTO_ATTACH instead of
minting duplicates**.

usa-wa's read model under #203:
- The subscription set is built by
  `GET /api/v1/subscriptions/discover?root_type=jurisdiction&root_id=usa-wa&follow=lineage,affiliated_orgs,org_children,roles,assignments,people`,
  registered via `POST /api/v1/subscriptions`, then changes flow off the filtered
  `/api/v1/changes` feed.
- So **everything usa-wa caches must be reachable from the `usa-wa` jurisdiction via those
  six edges**, and must carry the identifiers usa-wa keys on.

## Current re-seed state (observed 2026-06-16)

Discovery from `usa-wa` returns **1040** entities: `jurisdiction=1` (the `usa-wa` state root
only), `organization=41`, `role=245`, `role_assignment=418`, `person=335`. Two problems:

1. **County jurisdictions are missing.** usa-wa holds 100 `usa-wa-county-*` jurisdictions;
   `follow=lineage` returns **0 descendants**, so none are discoverable.
2. **Identifier coverage on the org/person tree is unverified.** usa-wa's local producer cache
   is currently **empty**, so usa-wa **cannot re-push** the org/role/assignment/person tree
   after a reset — PM must hold it. For usa-wa to relink (and to avoid duplicates when
   usa-wa's adapter later produces this data), the re-seeded records must carry the canonical
   identifiers below.

## What each entity type must carry (for AUTO_ATTACH + discovery)

| Entity | Identifier type slug(s) usa-wa keys on | Discovery edge that must resolve |
|---|---|---|
| jurisdiction | `jur_slug` (e.g. `usa-wa`, `usa-wa-county-king`) | root; `lineage` → descendant jurisdictions (counties) |
| organization | `org_wa_legislature_committee_id` (committees), `org_wa_legislature_chamber` (chambers), `org_wa_pdc` | `affiliated_orgs` → orgs with a **`governing`** affiliation to `usa-wa`; `org_children` → child orgs via `parent_id` |
| role | (structural — matched via its org) | `roles` → roles of a matched org |
| person | `person_wa_legislature_member_id`, `person_wa_pdc` | `people` → persons of a matched assignment |
| role_assignment | (structural — person + role) | `assignments` → assignments of a matched role |

Additional expectations from usa-wa's observation payloads:
- **Org names**: `name_type: "legal"`; org affiliation to the jurisdiction uses affiliation
  type **`governing`**.
- **Person names**: `name_type: "legal"`.
- **Assignments** carry `start_date` / `end_date` / `is_current` and link `person_id` ↔ `role_id`.
- **Jurisdictions** carry `jurisdiction_type_slug` (e.g. `state`, the county type) so the type resolves.

## Why this matters

usa-wa's producer descriptors AUTO_ATTACH by the identifier slugs above (`pm_match` cascade:
exact identifier → name → hierarchy; plus enrich-on-match keyed on `pm_org_id` /
`pm_person_id`). If a re-seed lacks these identifiers, a later usa-wa observation will **not**
match the re-seeded record and will **mint a duplicate**. Carrying the identifiers makes
re-seeds idempotent against usa-wa's eventual production.

## Requested

1. Confirm/ensure the re-seeded 41 orgs / 245 roles / 418 assignments / 335 persons carry the
   identifier slugs in the table above.
2. Decide ownership of the `usa-wa-county-*` jurisdictions: either PM re-seeds them (with
   `jur_slug` + `lineage` parentage under `usa-wa`), or usa-wa re-creates them via observation
   (usa-wa has cleared the stale anchors so its sidecar will re-observe them keyed on
   `jur_slug`; AUTO_ATTACH dedupes if PM also seeds them).
3. Ensure the `lineage`, `affiliated_orgs` (`governing`), `org_children` (`parent_id`),
   `roles`, `assignments`, `people` edges resolve for the re-seeded tree so discovery returns
   the full subtree.

## References
- Downstream: CannObserv/usa-wa#10 (sidecar adaptation, shipped)
- Upstream: #203 (per-key change-feed subscriptions), #191 (original request)
