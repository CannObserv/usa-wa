# Canonical Jurisdiction Decoupling — Design

- **Status:** IMPLEMENTED 2026-06-09 (models + migration `8d3f5cb3248f`; 24 tables; applied to prod at head).
- **Issue:** [CannObserv/usa-wa#4](https://github.com/CannObserv/usa-wa/issues/4) (prerequisite for step 6c identity descriptors; corrects the v1.4 jurisdiction model).
- **Related:** [`docs/specs/2026-06-09-identity-sync-design.md`](2026-06-09-identity-sync-design.md) (surfaced this), [`docs/specs/2026-05-31-jurisdictional-ia-design.md`](2026-05-31-jurisdictional-ia-design.md) (the v1.4 refactor being corrected).

## Problem

The canonical schema treats `jurisdiction_id` as a **required, identity-defining** field (NOT NULL FK, embedded in `(jurisdiction_id, source, source_id)` natural keys) across the identity, vote, bill, and session clusters. That over-applies jurisdiction: most of these entities relate to a jurisdiction only **through a public Organization**, not intrinsically.

- **People** never belong to a jurisdiction.
- **Organizations** belong to one only *optionally* — a *public* org may (WA Legislature → `usa-wa`); *private* orgs are global.
- **Roles, assignments, entity-events, votes, bills, and sessions** are jurisdiction-bound only *transitively*, through the public organization they hang off (a chamber, or the legislature).
- Only **true jurisdiction reference data** — statutes and per-jurisdiction vocabularies — is intrinsically jurisdiction-keyed.

Surfaced while building the step-6c PM sync descriptors: PM's person/org reads carry **no jurisdiction**, and PM is adding org jurisdiction as an **optional** column ([power-map#194](https://github.com/CannObserv/power-map/issues/194)). The local NOT NULL assumption mis-models the domain and can't be satisfied from PM. (`jurisdiction_id` is also redundant in the natural keys — `source`, e.g. `usa_wa_legislature`, already encodes the system.)

## Corrected model — where jurisdiction lives

| Entity group | Jurisdiction | Derivation path |
|---|---|---|
| **Organization** | stored, **nullable** (binding root; public orgs only) | — |
| Person | none | — |
| Role / Assignment / EntityEvent | derived | role → org; assignment → role → org; event → parent org (person-events: none) |
| Vote cluster (VoteEvent / VoteCount / PersonVote) | derived | vote_event.`context_organization_id` → org; counts/person-votes → vote_event |
| **Bill instance** tables (13, below) | derived | bill.`originating_chamber_id` → org; children → … → bill → chamber |
| **LegislativeSession** | derived | **new required** `organization_id` → the **WA Legislature org** |
| Bill **vocab** (`bill_types`, `bill_relationship_types`) | **stored** (jurisdiction reference vocab) | — |
| **Statutes** (`statute_*`) | **stored** (jurisdiction law) | — |
| PDC cluster (`lobbying_*`, `contributions`) | **out of scope** (deferred to the PDC adapter) | — |

**Natural keys** drop `jurisdiction_id` on every decoupled table, collapsing to `(source, source_id)` (still unique — `source` encodes the system). Secondary keys lose `jurisdiction_id` too (e.g. roles' `(jurisdiction_id, organization_id, name)` → `(organization_id, name)`).

**New requirement — the WA Legislature is an Organization.** `LegislativeSession` gains a **NOT NULL** `organization_id` FK to the legislature org (the parent of the House/Senate chambers). A WA Legislature `Organization` row must exist before any session. (Chambers are already `org_type='chamber'` with `parent_organization_id`; the legislature is that parent. The `org_type` vocab has no `legislature` value today — add `legislature` or use `government_agency`; settled at seed time, noted in the plan.)

## In-scope tables (24) — all empty (verified 2026-06-09 → pure schema change, no data migration)

**Identity (7):** `organizations` (jurisdiction → nullable), `persons`, `roles`, `assignments`, `entity_events`, `person_identifiers`, `organization_identifiers` (drop).
**Vote (3):** `vote_events`, `vote_counts`, `person_votes` (drop).
**Bill instance (13):** `bills`, `bill_sponsorships`, `bill_actions`, `bill_action_classifications`, `bill_versions`, `bill_titles`, `amendments`, `bill_subjects`, `bill_relationships`, `bill_events`, `bill_version_links`, `bill_statutory_citations`, `bill_supplements` (drop).
**Session (1):** `legislative_sessions` (drop jurisdiction; **add** required `organization_id`).

Every bill-instance table reaches a chamber org transitively (verified): direct `bill_id`, or via `bill_action_id` / `bill_version_id` → bill → `originating_chamber_id`. `bill_events.bill_id` is `SET NULL`, so its jurisdiction is best-effort (also has a nullable `organization_id`).

## Out-of-scope tables (retain NOT NULL `jurisdiction_id`)

`bill_types`, `bill_relationship_types` (per-jurisdiction vocab); `statute_codes` / `statute_titles` / `statute_chapters` / `statute_sections` (jurisdiction law). PDC cluster (`lobbying_activities`, `lobbying_positions`, `contributions`) untouched — deferred.

## Natural-key changes (representative)

| Table | Old key(s) with `jurisdiction_id` | New |
|---|---|---|
| `organizations` | `(jurisdiction_id, source, source_id)` | `(source, source_id)` |
| `persons` / `assignments` / `entity_events` / `vote_*` / most bill children | `(jurisdiction_id, source, source_id)` | `(source, source_id)` |
| `roles` | `(jur, source, source_id)`, `(jur, organization_id, name)` | `(source, source_id)`, `(organization_id, name)` |
| `person_identifiers` / `organization_identifiers` | `(jur, source, source_id)`, `(jur, scheme, value)` | `(source, source_id)`, `(scheme, value)` (keep `(parent_id, scheme)`) |
| `legislative_sessions` | `(jur, source, source_id)`, `(jur, slug)` | `(source, source_id)`, `(organization_id, slug)` |
| `vote_counts` | `(jur, source, source_id)` | `(source, source_id)` (keep `(vote_event_id, count_type)`) |

(Full per-table list lives in the plan.)

## Migration

- Single Alembic revision (large but mechanical; tables empty → no data move). Autogenerate, then hand-verify drop/alter ordering and the `legislative_sessions.organization_id` add.
- Per decoupled table: drop jurisdiction-keyed unique constraint(s) → create the `(source, source_id)` (and secondary) constraints → drop `jurisdiction_id` FK + index + column. `organizations`: alter column to nullable (keep FK/index). `legislative_sessions`: add `organization_id` NOT NULL FK + index.
- Downgrade is structural-only (empty tables; documented limitation — re-imposing NOT NULL on a repopulated table would need a default).
- `alembic upgrade head`; live `usa-wa` API + PM sidecar unaffected (they touch `clearinghouse_core.jurisdictions`, not these tables).

## Blast radius

`jurisdiction_id` on these entities is referenced only in the models (`identity.py`, `votes.py`, `bills.py`, `sessions.py`) and `test_identity_pm_anchors.py`. No API route, adapter, runner, or query depends on it (these adapters/endpoints aren't built yet). The jurisdiction descriptor and PM sidecar are untouched.

## Testing strategy (TDD)

- **Red first:** update `test_identity_pm_anchors.py` to build entities without `jurisdiction_id` (orgs optionally with it) — fails on the current NOT NULL model.
- Add model/constraint tests: person persists with no jurisdiction; org persists with `jurisdiction_id=None` and with a value; a session requires `organization_id`; natural-key uniqueness now keys on `(source, source_id)`; a role/bill/vote's jurisdiction is reachable via its org through a join.
- `-m integration` round-trip (real Postgres, savepointed) exercising the new constraints + the session→org requirement.
- Green: model edits + migration; `alembic upgrade head` on the test DB via the suite.

## Open questions / risks

1. **`org_type` vocab for the WA Legislature** — add a `legislature` value or reuse `government_agency`. Settled at seed time; does not block the schema change.
2. **Session → legislature-org seeding order** — a session now requires the legislature org to exist first; the adapter/seed must create it before sessions. (No issue while empty.)
3. **PDC/lobbying cluster** — same transitive pattern; deferred to the PDC adapter workstream.
4. **`source` global uniqueness** — relies on `source` encoding the system (`usa_wa_*`); low risk, noted.
5. **Downgrade fidelity** — structural-only (empty tables), agreed limitation.
