# Identity Sync Design Addendum — persons / orgs / roles / assignments (step 6c)

- **Status:** proposed — review before implementation. *Revised 2026-06-09 after a data-model correction (see below).*
- **Extends:** [`docs/specs/2026-06-02-power-map-sync-sidecar-design.md`](2026-06-02-power-map-sync-sidecar-design.md) (sidecar engine + jurisdiction descriptor, live) and [`docs/specs/2026-05-27-power-map-integration.md`](2026-05-27-power-map-integration.md) (identifier-type scheme).
- **Issue:** [CannObserv/usa-wa#4](https://github.com/CannObserv/usa-wa/issues/4)

## Why this addendum

Building the four identity descriptors against the deployed PM surface surfaced coordination gaps the sidecar spec (written jurisdiction-first) didn't cover — and, more importantly, revealed that **the identity entities are not jurisdiction-scoped the way jurisdictions are**. This pins the corrected model and the coordination so the descriptors can be built without guessing.

PM read/write shapes (verified against the generated client, 2026-06-09):

| Entity | Read model fields | `updated_at`? | Reconcile? | Feed type | Observation match key |
|---|---|---|---|---|---|
| person | `id, display_name, archived_at, names[], identifiers[]` | **NO** | feed-only | `person` | `identifier_type` + `identifier_value` |
| organization | `id, name, acronym, slug, parent_id, archived_at, names[], acronyms[], identifiers[]` | **NO** | feed-only | `organization` | `identifier_type` + `identifier_value` |
| role | `id, organization_id, title, created_at, updated_at, …` | yes | list ✓ | `role` | `organization_id` (PM id) + `title` |
| assignment | `id, person_id, role_id, is_current, created_at, updated_at, start_date, end_date, …` | yes | list ✓ | `role_assignment` | `person_id` + `role_id` (PM ids) |

## Data-model correction (the load-bearing change)

Identity entities **do not** belong to a jurisdiction the way the local schema currently assumes:

- **Person — never has a jurisdiction.** A person is a human, not bound to any jurisdiction.
- **Organization — *optional* jurisdiction.** A *public* org may belong to one (WA Legislature → `usa-wa`); *private* orgs are global and have none. Never required. **PM has no org `jurisdiction_id` today** → [power-map#194](https://github.com/CannObserv/power-map/issues/194) (add optional column + backfill).
- **Role / Assignment — *transitive* jurisdiction** only, via their associated public Organization.

This contradicts the current local schema in [`clearinghouse_domain_legislative/identity.py`](../../packages/clearinghouse-domain-legislative/src/clearinghouse_domain_legislative/identity.py), where `Person/Organization/Role/Assignment` (and the identifier/event children) all carry a **NOT NULL `jurisdiction_id`** and **jurisdiction-keyed natural keys** (`uq_*_natural_key = (jurisdiction_id, source, source_id)`).

**Prerequisite local schema correction (usa-wa-side, before any identity descriptor):**

| Table | `jurisdiction_id` | Natural key |
|---|---|---|
| `persons` | **drop** (people have no jurisdiction) | `(source, source_id)` |
| `organizations` | **nullable** (public orgs only) | `(source, source_id)` |
| `roles` | nullable, or derive transitively from org | `(source, source_id)`, `(organization_id, name)` |
| `assignments` | nullable, or derive transitively from role | `(source, source_id)` |
| `person_identifiers` / `organization_identifiers` / `entity_events` | follow parent | drop `jurisdiction_id` from natural key |

This is a focused migration + model change that cascades across the identity cluster; it deserves its own design/plan pass (it is not a descriptor detail).

## D1 — Person/Org observation keying (`identifier_type`) — corrected framing

PM matches person/org observations on a single `identifier_type` + `identifier_value`. The existing PM slugs (`person_wa_legislature_member_id`, `org_wa_legislature_committee_id`, `*_wa_pdc`) encode **entity + producing *system* + key** — the `wa_legislature` segment is the *organization/system*, **not** a jurisdiction. So do **not** derive the key from jurisdiction; treat `identifier_type` as an opaque per-source slug.

Mapping stays a `source → identifier_type` table (mechanically unchanged), `identifier_value = local source_id`:

| Entity | local `source` (+ `org_type`) | PM `identifier_type` |
|---|---|---|
| person | `usa_wa_legislature` | `person_wa_legislature_member_id` |
| person | `usa_wa_pdc` | `person_wa_pdc` |
| organization | `usa_wa_legislature`, `org_type=committee` | `org_wa_legislature_committee_id` |
| organization | `usa_wa_legislature`, `org_type=chamber` | `org_wa_legislature_chamber` |
| organization | `usa_wa_pdc` | `org_wa_pdc` |

- Full local identifier graph rides along as `additional_identifiers[]`; the top-level pair is only the match key.
- **Coordination:** confirm all five slugs are seeded in PM's `entity_identifier_types` (#157 seeded most; verify `org_wa_legislature_chamber`). Unknown key → `rejected` on the outbox, not a silent failure.

## D2 — persons/orgs deferred until PM `updated_at` ships

PersonDetail/OrgDetail expose **no `updated_at`**, so LWW has no remote clock — the same no-parity condition behind the jurisdiction write-back loop. **Decision: build nothing for persons/orgs this increment.** They are gated on [power-map#193](https://github.com/CannObserv/power-map/issues/193) (add `updated_at`/`created_at` to person/org reads). The feed-`changed_at` interim is rejected as too fragile. Roles/assignments are unaffected (native `updated_at`).

## D3 — cohort selection (replaces "jurisdiction_id resolution")

With the corrected model, the cohort is rooted in **organizations**, not a per-row jurisdiction:

- Select PM organizations where `jurisdiction_id == usa-wa` (requires [power-map#194](https://github.com/CannObserv/power-map/issues/194)).
- Pull their **roles** (via `organization_id`) and **assignments** (via `role_id`) transitively → a deterministic WA cohort.
- **Persons** enter the cohort only transitively (assigned to a WA role); they are deferred (D2) regardless.

## D4 — Unanchored-dependency ordering (roles / assignments)

Roles need the org's `pm_organization_id`; assignments need `pm_person_id` + `pm_role_id`. Add an engine seam:

- `EntityDescriptor.dependencies_ready(session, row) -> bool` (default `True`).
- `drain_outbox` consults it before delivery; if `False`, **leave the entry PENDING** and bump `next_attempt_at` (no delivery, no crash) so ordering self-resolves as parents anchor in later cycles.
- Pairs with [#11](https://github.com/CannObserv/usa-wa/issues/11) (defer, don't crash, on a not-ready condition).

## D5 — Sequencing (corrected: 6c is substantially gated)

The entities are interdependent through organizations, so there is **no buildable descriptor slice until the cohort root (orgs) is unblocked**:

| Gate | Unblocks |
|---|---|
| **Local identity schema correction** (drop/relax `jurisdiction_id`, re-key) | prerequisite for *all four* descriptors |
| [power-map#194](https://github.com/CannObserv/power-map/issues/194) — org `jurisdiction_id` + backfill | org cohort selection → transitively roles/assignments |
| [power-map#193](https://github.com/CannObserv/power-map/issues/193) — person/org `updated_at` | persons/orgs LWW (D2) |
| [power-map#159](https://github.com/CannObserv/power-map/issues/159) — identifier search | person/org anchor lookup + their *only* reconcile backstop |

Practical order once gates clear: **schema correction → orgs (read) → roles (read) → assignments (read) → activate writes** (roles/assignments first; persons last, after #193).

## Coordination summary

| Item | Status |
|---|---|
| Person/Org `updated_at` on reads | [power-map#193](https://github.com/CannObserv/power-map/issues/193) (filed) |
| Optional org `jurisdiction_id` + backfill | [power-map#194](https://github.com/CannObserv/power-map/issues/194) (filed) |
| Identifier-filtered people/orgs search | [power-map#159](https://github.com/CannObserv/power-map/issues/159) (open) — wire the `pmclient` wrapper (currently hardcodes `q=""`) |
| `org_wa_legislature_chamber` seeded? | confirm; seed-request if missing |

## The one unblocked piece

The **local identity schema correction** needs no PM coordination — it's the natural next concrete step and a hard prerequisite for the descriptors. Recommend taking it as its own design/plan pass (it cascades across the identity cluster's natural keys and FKs). Everything else in 6c waits on PM #193/#194/#159.
