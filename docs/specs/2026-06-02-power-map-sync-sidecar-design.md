# Power Map Sync Sidecar — Design

- **Status:** in implementation — engine (steps 1–4) shipped; refreshed 2026-06-05 against PM #176–179 as deployed.
- **Issue:** [CannObserv/usa-wa#4](https://github.com/CannObserv/usa-wa/issues/4)
- **Supersedes/extends:** the deferred sidecar note in [`docs/plans/2026-05-31-jurisdictional-ia-implementation.md`](../plans/2026-05-31-jurisdictional-ia-implementation.md); operationalizes project memories `project_sidecar_sync_pattern` + `project_identity_producer_archival` + `project_pm_observations_endpoints`.
- **Cross-refs:** [`docs/specs/2026-05-31-jurisdictional-ia-design.md`](2026-05-31-jurisdictional-ia-design.md) §2/§3 (sidecar read/write sketch, observation payloads), [`docs/specs/2026-05-27-power-map-integration.md`](2026-05-27-power-map-integration.md) (read surface + auth).

## Changelog (2026-06-05 — PM #176–179 shipped & deployed)

The four PM coordination issues (§7) landed; three changed the model from what this spec originally assumed. The portable engine (steps 1–4) is unaffected — these are descriptor-config + doc deltas only.

| Original assumption | Shipped reality | Source |
|---|---|---|
| **6 descriptors**, entity_events its own type with a dedicated `/entity-events` observation endpoint | **5 descriptors.** Entity events have **no standalone API** — they are embedded in person/org observations (`events: [ObservationEventItem]`), read via per-parent `GET /{people\|orgs}/{id}/events`, and change-detected by a parent-propagation trigger (event CUD bumps the parent's `updated_at`). Entity events are a **sub-resource of person & organization**, not a 6th entity. | PM #178 |
| Jurisdictions **not** on the `/changes` feed → reconcile-primary | Jurisdictions **are** on the feed (create+update) → **feed-primary**, reconcile as backstop. | PM #179 |
| Assignment feed discriminator `assignment` | Discriminator is **`role_assignment`** (route path is still `/assignments`). | PM #179 feed |
| Roles/assignments/entity_events write paths **dormant pending PM** | Roles + assignments observation endpoints **live** (`/roles/observations`, `/assignments/observations`); all 5 write paths are **activatable**. Only jurisdiction-write-first remains an MVP *choice*. | PM #176, #177 |

**Two follow-on consequences:**
- **`canonical.entity_events` is under-modeled** for PM's `ObservationEventItem` (granular partial dates `event_year/month/day/hour/...`, `event_type_slug` XOR id, `event_place_text`, `visibility`). Refinement needed before event sync is built; deferred (events are out of the MVP increment). Tracked as a follow-up issue.
- **Assignment observations key on `person_id` + `role_id` (PM ids)** → sync-ordering constraint: persons and roles must be anchored before assignments push.

## Problem

usa-wa now carries authoritative-shape FKs for jurisdictions and produces identity records (Person / Organization / Role / Assignment, plus lifecycle Entity Events). Two cohorts must flow to/from Power Map (PM):

- **Jurisdictions** — PM-authoritative; usa-wa keeps a local cache mirror.
- **Identity + entity events** — usa-wa-produced; PM is the long-term archival store.

Both share the same mechanism (producer/archival pattern, disposition vocab, retry semantics, systemd unit shape), so they are handled by **one** sidecar service spanning multiple entity types — not parallel implementations.

A second motivation shapes the design: **other CannObserv services (outside this repo) will also need to sync with PM.** The sync mechanism is therefore split from the usa-wa specifics so the engine is portable, without prematurely abstracting away from PM.

## Decision summary

| Decision | Choice |
|---|---|
| Service shape | Single multi-entity sidecar; one systemd unit |
| Process model | Long-running async daemon (not cron one-shot) |
| Packaging | Portable engine package + usa-wa binding package (clean seam) |
| Entity types | **5 descriptors**: jurisdictions, persons, organizations, roles, assignments. Entity events are a sub-resource of person/org (embedded write + per-parent read), not a descriptor. |
| Write delivery | In-DB outbox table (`sync.powermap_outbox`) |
| Read mechanism | PM `/changes` feed (incremental — covers jurisdiction/person/organization/role/role_assignment) + periodic full-reconcile backstop |
| Conflict resolution | PM is system-of-record; contested fields resolved by last-updated-timestamp (LWW); ties → PM |
| Anchor columns | Standardize to `pm_<entity>_id` across the schema |
| Auth | `X-API-Key` header (validated against PM source 2026-06-02; the `Bearer` mention in IA design §3 is wrong) |
| MVP increment | Read-flow (all 5 types via feed + reconcile backstop) + live jurisdiction write; persons/orgs/roles/assignments writes activatable (endpoints live). Entity-event sync deferred (table refinement pending). |

### Validated PM public API state (source inspection, 2026-06-05 — post #176–179)

| Entity | Public READ | Observation (WRITE) | Feed `entity_type` |
|---|---|---|---|
| jurisdictions | ✅ `GET /api/v1/jurisdictions…` | ✅ `POST /jurisdictions/observations` | ✅ `jurisdiction` |
| people | ✅ + `GET /people/{id}/events` | ✅ `POST /people/observations` (embeds `events[]`) | ✅ `person` |
| orgs | ✅ + `GET /orgs/{id}/events` | ✅ `POST /orgs/observations` (embeds `events[]`) | ✅ `organization` |
| roles | ✅ `GET /api/v1/roles…` | ✅ `POST /roles/observations` | ✅ `role` |
| assignments | ✅ `GET /api/v1/assignments…` | ✅ `POST /assignments/observations` | ✅ `role_assignment` |
| entity events | per-parent sub-route only | embedded in person/org observations | — (parent bumps) |

The `/changes` feed emits create+update for all five entities plus generic deletes (`deleted_entities`). All observation endpoints are live; every dormant path from the original spec is now activatable. Entity events ride with their parent person/org.

## Section 1 — Architecture & package boundaries

The system splits along a **portable engine / local binding** seam, mirroring the repo's existing framework-vs-deployment layering.

### `clearinghouse-sync-powermap` (NEW — framework layer, sibling-portable)

The extractable artifact. Knows PM and the sync mechanism; knows nothing about WA, the legislative domain, or which concrete tables exist. **Zero usa-wa imports.** Test of the seam: `SyncEngine` is readable without knowing usa-wa exists.

Contents:

- **`EntityDescriptor`** — the entire per-entity contract a sibling implements:
  - `entity_type: str`
  - `read_path: str` · `observe_path: str` (PM endpoints)
  - `model` (SQLAlchemy class) · `anchor_column: str` (e.g. `pm_jurisdiction_id`) · `natural_key: tuple[str, ...]`
  - `authority: Literal["pm", "local"]` (producer side; bias only — see §3 conflict rules)
  - `reconcile_cadence` (per-entity full-reconcile interval; default hourly)
  - `to_observation(row) -> dict` — build the observation payload
  - `upsert_from_pm(session, record) -> None` — natural-key upsert + anchor set
  - `last_updated(row_or_record) -> datetime` — LWW comparator source
  - `write_enabled: bool` — gates the write path (dormant types ship registered but inert)
- **`OutboxEntry`** — generic outbox model, `entity_type`-discriminated (one table).
- **`SyncState`** — feed cursor + last-reconcile stamp persistence.
- **`SyncEngine`** — the daemon brain: changes-feed loop, full-reconcile loop, outbox worker, LWW reconciler, disposition handler, backoff. Pure over a `list[EntityDescriptor]`.
- **`PowerMapClient`** — thin wrapper over the `openapi-python-client`-generated client (per `project_sidecar_sync_pattern`; forward-compatible with PM's eventual SDK). Owns auth (`X-API-Key`) + base URL.

### `usa-wa-sync-powermap` (NEW — deployment layer, usa-wa-specific)

The binding + the runnable.

- The **5 concrete `EntityDescriptor`s** wiring `clearinghouse_core.jurisdictions` + `canonical.{persons, organizations, roles, assignments}`. The assignment descriptor's `entity_type` is **`role_assignment`** (the feed discriminator), though its routes live under `/assignments`. Entity events are **not** a descriptor — the person and organization descriptors own them: `to_observation` embeds `events: [ObservationEventItem]`, and `upsert_from_pm` pulls `GET /{people|orgs}/{id}/events` to refresh the `canonical.entity_events` mirror.
- **Descriptor `fetch_record` seam (added 2026-06-05):** because syncing person/org events needs a supplementary sub-resource fetch the descriptor can't do alone, the engine resolves a feed item to a full record via `descriptor.fetch_record(client, pm_id)` (default delegates to `client.get_entity`; person/org override to also fetch events). Keeps entity-specific composition in the descriptor.
- **Scope config** (`slug_prefix=usa-wa`, the usa-wa jurisdiction set for identity scoping), env loading.
- **`__main__` async daemon entrypoint** — `configure_logging()` once, builds descriptors, starts `SyncEngine`.
- **systemd unit** `usa-wa-sync-powermap.service` — own lifecycle, restart policy, env loading (`/etc/usa-wa/.env` + repo `.env`), separate from `usa-wa.service`.

### Deliberately NOT abstracted (YAGNI)

The engine stays **PM-specific** — PM's observation/disposition vocab and `/changes` feed are baked in, not hidden behind a generic "archival target" interface. When a second archival target (Archiver) appears, extract then.

## Section 2 — Data model & migration

### Outbox — `sync.powermap_outbox` (new `sync` schema)

Model lives in the portable package; side-effect-imported into `Base.metadata` (like `jurisdictions`/`provenance`).

| Column | Type | Note |
|---|---|---|
| `id` | ULID PK | |
| `entity_type` | str | discriminator → selects descriptor |
| `local_id` | ULID | source row PK |
| `op` | enum `CREATE` / `UPDATE` | |
| `status` | enum `PENDING` / `DELIVERED` / `REJECTED` | |
| `attempts` | int | backoff counter |
| `next_attempt_at` | timestamptz | worker skips until due |
| `last_disposition` | str nullable | `AUTO_ATTACHED` / `NEW` / `REJECTED` |
| `last_error` | text nullable | structured error for operator |
| `created_at` / `updated_at` | timestamptz | `TimestampMixin` |

- **No payload stored** — the worker re-reads the source row + calls `descriptor.to_observation()` at send time (never ships stale data).
- **At-most-one-open per row:** partial unique index on `(entity_type, local_id) WHERE status = 'PENDING'`. Re-enqueue of an already-pending row is a no-op (idempotency, issue constraint #5).
- `REJECTED` rows remain queryable — the operator backlog view (issue constraint #7).

### Sync state — `sync.powermap_sync_state`

Small table: changes-feed cursor + last full-reconcile timestamp (per entity type or per feed). Portable package.

### Anchor columns + naming standardization

Today's convention is inconsistent: `pm_jurisdiction_id` (clearinghouse_core) vs `powermap_person_id` / `powermap_organization_id` (canonical). The engine keys on a uniform `anchor_column`, so standardize to **`pm_<entity>_id`** in one Alembic revision:

- **Rename:** `powermap_person_id → pm_person_id`, `powermap_organization_id → pm_organization_id`.
- **Add:** `canonical.roles.pm_role_id`, `canonical.assignments.pm_assignment_id` (nullable, indexed).
- **Keep:** `pm_jurisdiction_id` (already conformant).
- **Entity events:** `canonical.entity_events.pm_entity_event_id` (added with the new table, below).

Renames touch `identity.py` and any reader; blast radius is small (identity sync is not yet live).

### Entity Events — `canonical.entity_events` (new table)

> **⚠️ Under-modeled (2026-06-05).** This is the table as shipped in the step-1 migration. PM #178's `ObservationEventItem` is richer than assumed: granular partial dates (`event_year/month/day/hour/minute/second`, any of which may be null — e.g. "born 1970"), `event_type_slug` XOR `event_type_id`, `event_place_text`, `visibility` (`public`/`legal_only`/`hidden`), and an optional `linked_entity`. The single `date` column cannot represent partial dates. **Refine before wiring event sync** (step 6b); table is unused at MVP so the change is non-breaking. The shape below is retained for history.

Original shape (pre-#178-reassessment):

| Column | Type | Note |
|---|---|---|
| `id` | ULID PK | |
| `jurisdiction_id` | ULID FK | scope, consistent with other canonical tables |
| `source` / `source_id` | str | natural-key components |
| `entity_kind` | str | `person` / `organization` |
| `entity_id` | ULID FK | → `canonical.persons.id` or `canonical.organizations.id` |
| `event_type` | str | `birth` / `death` / `founding` / `dissolution` / … |
| `date` | date nullable | event date |
| `pm_entity_event_id` | ULID nullable, indexed | anchor |
| `created_at` / `updated_at` | timestamptz | `TimestampMixin` |

Natural-key UNIQUE: `(jurisdiction_id, source, source_id)`, consistent with the other canonical tables.

### LWW timestamp source

The reconciler compares `descriptor.last_updated(local_row)` vs `descriptor.last_updated(pm_record)`:

- **Local side** = `updated_at` (`TimestampMixin`).
- **PM side** = `recorded_at` for jurisdictions; PM's own `updated_at` for identity/events (the descriptor encapsulates which field).
- Both UTC. **Tie-break: equal timestamps → PM wins** (system-of-record fallback).
- **Assumption:** both clocks are trusted UTC; no skew correction at MVP. Documented limitation.

### Migration

One autogenerated Alembic revision: 2 renames + 3 column adds + `entity_events` table + `sync` schema + `powermap_outbox` + `powermap_sync_state`.

## Section 3 — Daemon & sync flows

Process model B: one async daemon (`usa-wa-sync-powermap.service`) running concurrent loops over the shared descriptor list, plus a boot pass.

### Boot pass (once, on start)

Full read reconcile for every entity type PM serves → drain outbox. Seeds/repairs the cache before steady state.

### Read flow (PM → local cache)

**Primary — changes-feed loop:**

1. `GET /api/v1/changes?since=<cursor>` for incremental deltas.
2. Dispatch each change to the matching descriptor's `upsert_from_pm()` + LWW reconcile (below).
3. Persist the new cursor to `sync.powermap_sync_state`.

> **Validated coverage (2026-06-05, post #176–179):** the feed emits create+update for `jurisdiction`, `person`, `organization`, `role`, and `role_assignment` (plus generic deletes). All five entities are **feed-primary**; the full reconcile is the backstop. Entity-event changes surface as bumps to their parent person/organization (parent-propagation trigger), so they ride the person/org feed entries.

**Backstop — periodic full reconcile** (per-entity `reconcile_cadence`, hourly default):

1. `GET <read_path>?slug_prefix=usa-wa&valid_at=…&cursor=…` paginated; jurisdictions filter on scope, identity/events on the usa-wa jurisdiction set.
2. `descriptor.upsert_from_pm()` per record.
3. Self-heals feed gaps / cursor loss; re-anchors.

**LWW reconcile step** (both paths): if the local row has unpushed edits (`updated_at` newer than PM's last-updated *and* the row is anchored), do not blindly overwrite — compare timestamps:

- PM newer → overwrite cache.
- Local newer → enqueue an `UPDATE` outbox entry to push local up.
- Equal → PM wins.

**Bitemporal mirror** for jurisdictions: `valid_from` / `valid_until` / `recorded_at` / `superseded_at` copied from PM's clock (distinct from local `created_at`/`updated_at`).

### Write flow (local → PM, outbox worker)

1. Poll `sync.powermap_outbox` for `status = PENDING AND next_attempt_at <= now`, short interval (process-B responsiveness).
2. Per entry: re-read source row → `descriptor.to_observation()` → `POST <observe_path>` (skipped if `write_enabled` is false — dormant types).
3. Disposition handling:
   - `AUTO_ATTACHED` / `NEW` → write returned `pm_<entity>_id` to source row; mark `DELIVERED`.
   - `REJECTED` → `status = REJECTED`, `last_error` set, structured error log + operator notification; source row stays un-anchored for manual PM admin action.
   - HTTP / network error → `attempts++`, exponential `next_attempt_at` backoff, stays `PENDING`.

**Enqueue triggers:**

- **Un-anchored sweep** — a periodic pass finds source rows with `pm_<entity>_id IS NULL` and enqueues `CREATE`. The adapter stays ignorant of the sidecar (no explicit enqueue coupling).
- **LWW step** — enqueues `UPDATE` when local is newer than PM for an anchored row.

### Failure decoupling

PM unreachable → read loop logs + retries next cycle; write loop leaves entries `PENDING` with backoff. **Adapter ingestion never blocks** — it only writes local rows; the sidecar observes them out-of-band (issue constraint #7).

## Section 4 — Error handling & observability

- **Transient (network / 5xx / PM down):** outbox backoff (`attempts++`, exponential `next_attempt_at`), stays `PENDING`; read loop retries next cycle.
- **`REJECTED`:** terminal status, `last_error` captured, structured `get_logger` error + operator notification; row left un-anchored.
- **Feed cursor loss / gap:** the periodic full reconcile is the self-heal.
- **Structured logging** via `clearinghouse_core.logging`; `configure_logging()` called once in the daemon entrypoint, never in engine library modules.

## Section 5 — Testing strategy

- **Engine unit tests** (`clearinghouse-sync-powermap`): descriptor contract, outbox state machine, LWW tie-break, backoff math — against a fake descriptor + mocked PM, **zero usa-wa deps** (proves portability).
- **`respx`-mocked PM** for the changes feed, full-reconcile reads, observation POST, and the three dispositions (per IA spec testing strategy).
- **Integration** (`-m integration`, real Postgres): savepointed sessions; full boot → read → reconcile → write → anchor round-trip for jurisdictions.
- TDD red → green → refactor throughout. No production code without a failing test first.

## Section 6 — MVP increment & sequencing

**First increment:**

1. Migration (anchor standardization + `entity_events` + `sync` schema + outbox + sync_state). ✅ shipped.
2. `clearinghouse-sync-powermap` engine + portable models. ✅ shipped (steps 1–4).
3. PM client (`PowerMapClient` over the generated SDK).
4. `usa-wa-sync-powermap` binding: 5 descriptors, daemon entrypoint, systemd unit.
5. **Read-flow live for all five types** (feed-primary + reconcile backstop).
6. **Write-flow live for jurisdictions** against `POST /api/v1/jurisdictions/observations`.
7. Persons/orgs/roles/assignments write paths **activatable** (all endpoints live) — enable in this increment or the next per appetite.

**Deferred (not dormant — PM-ready, but usa-wa-side work remains):** entity-event sync, pending the `canonical.entity_events` table refinement (granular partial dates etc.). The person/org descriptors carry a no-op events hook until then.

## Section 7 — PM coordination (issues shipped 2026-06-05)

All four issues filed 2026-06-02 are **CLOSED and deployed**. Outcomes (some diverged from the original ask — see Changelog):

1. **Roles public API** ([power-map#176](https://github.com/CannObserv/power-map/issues/176)) — ✅ `GET /api/v1/roles…` + `POST /api/v1/roles/observations`.
2. **Assignments public API** ([power-map#177](https://github.com/CannObserv/power-map/issues/177)) — ✅ `GET /api/v1/assignments…` + `POST /api/v1/assignments/observations`. Feed discriminator is `role_assignment`.
3. **Entity-events** ([power-map#178](https://github.com/CannObserv/power-map/issues/178)) — shipped as a **parent-propagation trigger** (event CUD bumps the parent's `updated_at`) + observation embedding + per-parent read route, **not** a standalone `/entity-events` API. Events are a person/org sub-resource.
4. **Changes-feed coverage** ([power-map#179](https://github.com/CannObserv/power-map/issues/179)) — ✅ emits `jurisdiction` create+update; the feed now covers all five entities.

**Auth resolved (no issue):** `X-API-Key` validated in PM `src/api/public/deps.py`; the `Authorization: Bearer` line in IA design §3 is wrong and should be corrected in a docs sweep.

**Outstanding usa-wa follow-up:** refine `canonical.entity_events` to PM's `ObservationEventItem` shape before wiring event sync ([usa-wa#9](https://github.com/CannObserv/usa-wa/issues/9)).

## Section 8 — Out of scope

- Adapter ingestion (separate workstream; sidecar consumes whatever rows the adapter writes).
- Spatial / geometry sync (deferred per IA design §1).
- Archiver content sidecar (deferred indefinitely per P0.5 spec).
- Clock-skew correction (LWW trusts both UTC clocks).

## Open questions

None blocking. PM coordination items (§7) are tracked as upstream issues; their resolution flips dormant write paths live without further usa-wa design changes.
