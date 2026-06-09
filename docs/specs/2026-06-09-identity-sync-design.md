# Identity Sync Design Addendum — persons / orgs / roles / assignments (step 6c)

- **Status:** proposed — review before implementation.
- **Extends:** [`docs/specs/2026-06-02-power-map-sync-sidecar-design.md`](2026-06-02-power-map-sync-sidecar-design.md) (the sidecar engine + jurisdiction descriptor, live) and [`docs/specs/2026-05-27-power-map-integration.md`](2026-05-27-power-map-integration.md) (identifier-type scheme).
- **Issue:** [CannObserv/usa-wa#4](https://github.com/CannObserv/usa-wa/issues/4)

## Why this addendum

Building the four identity descriptors against the deployed PM surface surfaced five coordination gaps not covered by the sidecar spec (which was written jurisdiction-first). This pins the five decisions so the descriptors can be built without guessing — the same frontload discipline that pinned `jur_slug` for jurisdictions.

PM read/write shapes (verified against the generated client, 2026-06-09):

| Entity | Read model fields | `updated_at`? | Reconcile? | Feed type | Observation match key |
|---|---|---|---|---|---|
| person | `id, display_name, archived_at, names[], identifiers[]` | **NO** | feed-only | `person` | `identifier_type` + `identifier_value` |
| organization | `id, name, acronym, slug, parent_id, archived_at, names[], acronyms[], identifiers[]` | **NO** | feed-only | `organization` | `identifier_type` + `identifier_value` |
| role | `id, organization_id, title, created_at, updated_at, …` | yes | list ✓ | `role` | `organization_id` (PM id) + `title` |
| assignment | `id, person_id, role_id, is_current, created_at, updated_at, start_date, end_date, …` | yes | list ✓ | `role_assignment` | `person_id` + `role_id` (PM ids) |

## D1 — Person/Org observation keying (`identifier_type`)

The scheme is already established (integration spec §"#157 seeds", slug pattern `<entity>_<jurisdiction>_<system>_<key>`). Carry it into the descriptors as a `source → identifier_type` map; `identifier_value = local source_id`:

| Entity | local `source` (+ `org_type`) | PM `identifier_type` |
|---|---|---|
| person | `usa_wa_legislature` | `person_wa_legislature_member_id` |
| person | `usa_wa_pdc` | `person_wa_pdc` |
| organization | `usa_wa_legislature`, `org_type=committee` | `org_wa_legislature_committee_id` |
| organization | `usa_wa_legislature`, `org_type=chamber` | `org_wa_legislature_chamber` |
| organization | `usa_wa_pdc` | `org_wa_pdc` |

- The full local identifier graph (`person_identifiers` / `organization_identifiers`) rides along as `additional_identifiers[]` so PM gets every scheme; the top-level pair is only the match key.
- **Coordination:** all five slugs above must exist in PM's `entity_identifier_types`. #157 seeded the `*_member_id` / `*_committee_id` / `*_pdc` set; `org_wa_legislature_chamber` appears in the integration spec example — **confirm it is seeded** (else file a PM seed request). An unknown match key returns `rejected` (surfaced on the outbox), not a silent failure.

## D2 — LWW remote clock for feed-only persons/orgs

PersonDetail/OrgDetail expose **no `updated_at`**, so `descriptor.last_updated(pm_record)` has nothing to read — the exact no-parity condition behind the jurisdiction write-back loop. Two-part resolution:

- **Interim (no PM change, unblocks 6c):** use the changes-feed `ChangeItem.changed_at` as the person/org LWW clock. Sound because persons/orgs are **feed-only** (no reconcile path that would lack a change item). Mechanism: `process_feed` stamps `changed_at` into the fetched record dict (e.g. `record["_feed_changed_at"]`) before `apply_record`; the person/org `last_updated` reads it for PM records. Roles/assignments keep using their own `updated_at`.
- **PM ask (clean long-term):** add `updated_at` (+ `created_at`) to PersonDetail/OrgDetail. Filed as [**power-map#193**](https://github.com/CannObserv/power-map/issues/193). Switch the descriptors to it when shipped and retire the interim.

## D3 — `jurisdiction_id` resolution on read

Local person/org/role/assignment carry a NOT NULL `jurisdiction_id`; no PM read model carries a jurisdiction reference. Resolution leans on the **producer model** — usa-wa mints these locally (jurisdiction known from the adapter), pushes to PM, anchors; the feed echo then re-matches the existing local row via anchor/natural-key, so `jurisdiction_id` is already set and preserved.

- **Foreign-origin records** (a sibling service's WA person with no local match): **skip-and-log** — usa-wa does not cache identity it cannot place. Consistent with the jurisdiction typeless-skip and the unfiltered-firehose concern ([#10](https://github.com/CannObserv/usa-wa/issues/10)).
- *Open question:* skip foreign records, or default them to the `usa-wa` state jurisdiction (the `slug_prefix=usa-wa` scope)? Recommend **skip** for the MVP (smaller blast radius; revisit if cross-service WA identity caching is wanted).

## D4 — Unanchored-dependency ordering (roles / assignments)

Roles need the org's `pm_organization_id`; assignments need `pm_person_id` + `pm_role_id`. The ordering (orgs→roles, persons+roles→assignments) is stated in the sidecar spec but had no mechanism. Add an engine seam:

- `EntityDescriptor.dependencies_ready(session, row) -> bool` (default `True`).
- `drain_outbox` consults it before delivery; if `False`, **leave the entry PENDING** and bump `next_attempt_at` (no delivery, no crash) so the ordering self-resolves once the parent anchors in a later cycle.
- Pairs with [#11](https://github.com/CannObserv/usa-wa/issues/11) (don't crash the cycle on a not-ready/permanent condition — defer it).

## D5 — Activation

Per the plan ("descriptors register inert; write per appetite"):

- **Read path (upsert + anchor) live for all four** on registration — this is the bulk of 6c and is unblocked once D2 (interim clock) + D3 land.
- **Write path** activated after D1 slugs are confirmed seeded and D4 ordering lands: roles/assignments first (they have native `updated_at`), persons/orgs once the D2 PM clock or interim is proven. Assignment writes additionally gate on persons+roles being anchored (D4).

## Coordination summary

| Item | Action |
|---|---|
| Person/Org `updated_at` on read models | **File PM issue** (D2) |
| Identifier-filtered people/orgs search (anchor lookup + only feed backstop) | **PM #159** (OPEN) — wire the `pmclient` wrapper to pass `identifier_type`/`identifier_value` (currently hardcodes `q=""`) |
| `org_wa_legislature_chamber` seeded? | Confirm; seed-request if missing (D1) |

## Open questions for reviewer

1. **D3** — skip foreign-origin identity, or default to the WA state jurisdiction?
2. **D5** — activate roles/assignments writes in this increment, or land all four read-only first and activate writes in a follow-up?
3. **D2** — accept the `changed_at` interim clock, or block person/org sync entirely until the PM `updated_at` ships?
