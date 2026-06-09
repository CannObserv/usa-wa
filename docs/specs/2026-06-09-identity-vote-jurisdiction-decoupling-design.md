# Identity & Vote Jurisdiction Decoupling — Design

- **Status:** proposed — review before planning.
- **Issue:** [CannObserv/usa-wa#4](https://github.com/CannObserv/usa-wa/issues/4) (prerequisite for step 6c identity descriptors).
- **Related:** [`docs/specs/2026-06-09-identity-sync-design.md`](2026-06-09-identity-sync-design.md) (the addendum that surfaced this), [`docs/specs/2026-05-31-jurisdictional-ia-design.md`](2026-05-31-jurisdictional-ia-design.md) (the v1.4 jurisdiction refactor this corrects).

## Problem

The canonical schema treats `jurisdiction_id` as a **required, identity-defining** field on the identity and vote clusters: a NOT NULL FK to `clearinghouse_core.jurisdictions`, embedded in every `(jurisdiction_id, source, source_id)` natural key. That is wrong for these entities:

- **People never belong to a jurisdiction** — a person is a human, not a jurisdiction member.
- **Organizations belong to a jurisdiction only *optionally*** — a *public* org may (WA Legislature → `usa-wa`), but *private* orgs are global. Never required.
- **Roles, Assignments, Entity-events, and Votes** are jurisdiction-bound only *transitively*, through the public organization they hang off.

This surfaced building the step-6c PM sync descriptors: PM's person/org read models carry **no jurisdiction reference at all**, and PM is *adding* org jurisdiction as an **optional** column ([power-map#194](https://github.com/CannObserv/power-map/issues/194)). The local NOT NULL assumption can't be satisfied from PM and mis-models the domain. (It also makes `jurisdiction_id` redundant in the natural keys, since `source` — e.g. `usa_wa_legislature` — already encodes the producing system.)

## Corrected model

Jurisdiction is stored in exactly one place per cluster and derived everywhere else:

- **Organization** — the binding root. `jurisdiction_id` becomes **nullable** (public orgs set it; private orgs leave it null). FK to `clearinghouse_core.jurisdictions` retained.
- **Person** — **no** jurisdiction. Column dropped.
- **Role / Assignment / EntityEvent** — **derive** via the org (`role.organization → org.jurisdiction_id`; `assignment.role.organization`; entity-event via its parent org; person-events have none). Column dropped.
- **Vote cluster** — `VoteEvent` derives via `context_organization_id → org`; `VoteCount` / `PersonVote` derive via their `vote_event`. Column dropped from all three.

**Natural keys** drop `jurisdiction_id` and collapse to `(source, source_id)` — still globally unique because `source` encodes the producing system/jurisdiction (`usa_wa_legislature`, `usa_or_legislature`, …).

### Out of scope (retain first-class `jurisdiction_id`)

Legislative artifacts and regulated filings are **intrinsically** jurisdiction-bound — a bill *is* a WA bill — and keep their NOT NULL `jurisdiction_id`:

- `bills` + all bill children, `amendments`, `statute_*`, `legislative_sessions`.
- `pdc` cluster (`lobbying_activities`, `lobbying_positions`, `contributions`) — WA-PDC-regulated filings. *Noted:* these touch orgs/persons and could warrant the same treatment later; deferred (not flagged in this correction).

## Table-by-table changes

All ten in-scope tables are **empty** (verified 2026-06-09) → pure schema change, no data migration.

| Table | `jurisdiction_id` | Natural-key change |
|---|---|---|
| `organizations` | NOT NULL → **nullable** (keep FK + index) | `uq_organizations_natural_key` → `(source, source_id)` |
| `persons` | **drop** (col + FK + index) | `uq_persons_natural_key` → `(source, source_id)` |
| `roles` | **drop** | `uq_roles_natural_key` → `(source, source_id)`; `uq_roles_org_name` → `(organization_id, name)` |
| `assignments` | **drop** | `uq_assignments_natural_key` → `(source, source_id)` |
| `entity_events` | **drop** | `uq_entity_events_natural_key` → `(source, source_id)` |
| `person_identifiers` | **drop** | `uq_person_identifiers_natural_key` → `(source, source_id)`; `uq_person_identifiers_jurisdiction_scheme_value` → `(scheme, value)` |
| `organization_identifiers` | **drop** | `uq_organization_identifiers_natural_key` → `(source, source_id)`; `…_jurisdiction_scheme_value` → `(scheme, value)` |
| `vote_events` | **drop** | `uq_vote_events_natural_key` → `(source, source_id)` |
| `vote_counts` | **drop** | `uq_vote_counts_natural_key` → `(source, source_id)` (keep `uq_vote_counts_event_type`) |
| `person_votes` | **drop** | `uq_person_votes_natural_key` → `(source, source_id)` (keep the person-or-name check) |

Unchanged on every table: `id` PK, `source`/`source_id`, the `pm_*_id` anchors, all non-jurisdiction columns and constraints.

Roles note: dropping `jurisdiction_id` collapses `uq_roles_org_name` to `(organization_id, name)` — district context (e.g. LD-21) lives on the **assignment/person**, not the Role slot ("Senator" is one Role under the Senate org). Consistent with the v1.4 intent.

## Deriving jurisdiction (for consumers)

No stored column on the derived entities → consumers join to the org:
`role.organization_id → organizations.jurisdiction_id`; `assignment.role_id → roles.organization_id → organizations.jurisdiction_id`; `vote_event.context_organization_id → organizations.jurisdiction_id`.

We do **not** add convenience hybrids/association-proxies now (YAGNI); add one if/when a query path needs it. The step-6c cohort selection ("orgs where jurisdiction = usa-wa, then their roles/assignments") is exactly these joins.

## Migration

- Single Alembic revision (autogenerate then hand-verify the drop/alter ordering).
- For each in-scope table: drop the jurisdiction-keyed unique constraint(s), recreate the `(source, source_id)` (and `(scheme,value)` / `(organization_id,name)`) constraints, drop the `jurisdiction_id` FK + index + column — except `organizations`, where the column is altered to `nullable=True` (constraint/index kept).
- No data migration (tables empty). Downgrade restores the columns/constraints (best-effort; the column would come back nullable-then-NOT NULL only if re-NOT-NULLed — document that downgrade is structural only).
- `alembic upgrade head`; the live `usa-wa` API + sidecar are unaffected (they touch `clearinghouse_core.jurisdictions`, not these tables).

## Blast radius

Confirmed minimal: `jurisdiction_id` on these entities is referenced only in the models (`identity.py`, `votes.py`) and one test (`test_identity_pm_anchors.py`). No API route, adapter, runner, or query depends on it (identity/vote adapters + endpoints are not built yet). The jurisdiction descriptor and PM sidecar are untouched.

## Testing strategy (TDD)

- **Red first:** update `test_identity_pm_anchors.py` to construct `Person`/`Organization`/etc. *without* `jurisdiction_id` (and for orgs, optionally with it) — fails against the current NOT NULL model.
- Add model/constraint tests: a `Person` persists with no jurisdiction; an `Organization` persists with `jurisdiction_id=None` and with a value; natural-key uniqueness now keys on `(source, source_id)`; a role's jurisdiction is reachable via its org.
- `-m integration` round-trip against real Postgres (savepointed sessions) for the new constraints.
- Green: model edits + migration; `uv run alembic upgrade head` on the test DB via the suite.

## Open questions / risks

1. **pdc/lobbying cluster** — same transitive pattern (lobbying firms/contributors are orgs/persons). Deferred; revisit when the PDC adapter is built. *Not* in this change.
2. **Downgrade fidelity** — since tables are empty, downgrade is structural only; re-imposing NOT NULL on a repopulated table would need a default. Documented as a limitation (irrelevant while empty).
3. **`source` global uniqueness** — relies on `source` encoding the jurisdiction/system (it does today: `usa_wa_*`). If a future source string were jurisdiction-ambiguous, `(source, source_id)` could collide. Low risk given the naming convention; noted.
