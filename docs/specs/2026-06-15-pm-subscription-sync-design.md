# PM Subscription Sync — Adapting the Sidecar to Power Map's Discovery/Subscription Model

**Date:** 2026-06-15
**Issues:** CannObserv/usa-wa#10 (downstream), CannObserv/power-map#203 / #191 (upstream, shipped)
**Status:** Approved

---

## Problem

Power Map shipped per-API-key change-feed subscriptions (power-map#203, verified live
in the OpenAPI). It is a **clean break** that breaks the usa-wa sidecar's read path:

1. `GET /api/v1/changes` no longer accepts `?since=<ISO timestamp>`. It now requires
   `?after=<int seq_id>` (`>` exclusive) and returns `meta.next_after` (integer). Our
   `GeneratedPowerMapClient.get_changes` still sends a `since` datetime → **broken against
   live PM today**.
2. The feed is **always subscription-filtered**. An empty subscription set returns an
   empty feed — there is no firehose fallback. Unless the sidecar registers
   subscriptions, it reads nothing.
3. Only `/changes` is filtered. The list/reconcile endpoints (`/api/v1/jurisdictions`,
   …) remain **unfiltered**, so the jurisdiction full-list reconcile still ingests all
   50 states — exactly the firehose #10 was filed against.

New PM surface to adopt:

- `GET /api/v1/subscriptions` — list own subscriptions (`entity_type`, `limit`, `offset`).
- `POST /api/v1/subscriptions` — bulk register `{"entity_ids": [...]}`, idempotent;
  returns `{registered, already_subscribed, not_found}`. Requires scope `subscriptions:write`.
- `DELETE /api/v1/subscriptions/{entity_id}` and `DELETE /api/v1/subscriptions` (bulk) —
  require `subscriptions:write`.
- `GET /api/v1/subscriptions/discover?root_type=&root_id=&follow=&limit=&offset=` —
  read-only graph traversal; returns `{entity_type, entity_id, display_name, hops_from_root}`.
  `follow` edges: `lineage`, `affiliated_orgs`, `org_children`, `roles`, `assignments`, `people`.

New subscriptions see only **future** changes; current state must be backfilled by direct
fetch-by-id. PM defers "saved searches" / auto-enroll to v2, so the **client** is
responsible for re-running discovery to catch graph drift.

---

## Goal

Restore and bound the sidecar read path under the new model. The read path moves from
**feed + unfiltered full-list reconcile** to **discovery (membership) + subscription-filtered
feed (changes) + fetch-by-id (backfill)**, all scoped to the WA subtree. The write path
(sweep → observe → outbox) is unchanged — subscriptions are a read-only concern.

---

## Approved Decisions

- **Subscription lifecycle: hybrid** — an explicit one-shot `bootstrap` command for initial
  population, plus a lightweight periodic re-discovery backstop in the sidecar loop to catch
  graph drift (a newly-added WA committee is picked up automatically).
- **Pruning: additive-only** — the backstop only ever registers + backfills newly-discovered
  entities. It never unsubscribes and never evicts local cache rows. This bounds local growth
  to the WA subtree (fixes #10) with no eviction-correctness surface. Pruning is a later
  issue if it ever matters.
- **Structure: portable mechanism, deployment-specific params (Approach A)** — the
  subscription/discovery mechanism lives in the sibling-reusable `clearinghouse-sync-powermap`
  package; the WA-specific discovery spec, bootstrap entrypoint, and sidecar wiring live in
  `usa-wa-sync-powermap`.

---

## Architecture

```
                          ┌─────────────── PM ───────────────┐
bootstrap (one-shot) ───► discover(root=jurisdiction:usa-wa,  │
                          follow=lineage,affiliated_orgs,     │
                          org_children,roles,assignments,     │
                          people)  ─► [entity ids+types]      │
                              │                               │
                              ├─► POST /subscriptions (new ids)│  ← subscriptions:write
                              └─► get_entity(id) per new id ──►│  backfill current state → apply_record (LWW)
                                                               │
sidecar loop (per cycle):                                      │
  re-discovery backstop (on cadence) ─────────────────────────┤  additive: register + backfill only NEW ids
  process_feed(after=<seq>) ──────────────────────────────────┤  filtered to subscribed ids; advances `after`
  sweep_unanchored + drain_outbox (write path) ── unchanged ───┘
```

- **Bootstrap** populates the subscription set + current state once (deploy step / manual,
  idempotent).
- **Feed** is the incremental primary for *all* entities now (subscription-filtered, so
  bounded). Cursor is the integer `after` seq.
- **Re-discovery backstop** replaces the jurisdiction full-list reconcile entirely. Runs on a
  cadence, additive-only, catches membership drift.
- **Backfill** = `get_entity(id)` for newly-registered ids only (the feed is forward-only).
  Bootstrap backfills all (registered set starts empty); the backstop backfills just the new.

---

## Components

### 1. PM client Protocol + cursor change (`clearinghouse-sync-powermap/client.py`, `pmclient.py`)

**Cursor type change (clean break):**

- `get_changes(since: str | None, …)` → `get_changes(after: int | None, …)`.
- `ChangePage.cursor: str | None` → `ChangePage.next_after: int | None`.
- `ChangeItem` unchanged (`entity_type`, `entity_id`, `changed_at`, `change_kind`).
- `GeneratedPowerMapClient.get_changes` passes `after` to the regenerated `get_change_feed`
  op and reads `feed.meta.next_after`. The `_EPOCH` constant and `since`→datetime parsing are
  deleted.

**New portable value types + Protocol methods:**

```python
@dataclass(frozen=True)
class DiscoveredEntity:
    entity_type: str       # 'jurisdiction'|'organization'|'role'|'role_assignment'|'person'
    entity_id: ULID
    display_name: str
    hops_from_root: int

@dataclass(frozen=True)
class SubscriptionResult:        # POST /subscriptions response
    registered: int
    already_subscribed: int
    not_found: Sequence[ULID]

class PowerMapClient(Protocol):
    async def get_changes(self, after: int | None, limit: int = 100) -> ChangePage: ...
    async def discover(self, *, root_type: str, root_id: str, follow: Sequence[str],
                       limit: int = 100, offset: int = 0) -> Sequence[DiscoveredEntity]: ...
    async def list_subscriptions(self, *, entity_type: str | None = None) -> Sequence[ULID]: ...
    async def add_subscriptions(self, entity_ids: Sequence[ULID]) -> SubscriptionResult: ...
    async def remove_subscriptions(self, entity_ids: Sequence[ULID]) -> int: ...  # unused; pruning deferred
```

`discover` and `list_subscriptions` paginate internally (PM `limit`/`offset`) until
exhausted. `GeneratedPowerMapClient` dispatches each to the regenerated SDK op, mapping
errors through the existing `_send` vocabulary (`RetryableClientError` / `DeliveryBlockedError`
/ `PayloadRejectedError`). `remove_subscriptions` is defined now to complete the surface but
is unused (pruning is deferred).

### 2. `SubscriptionReconciler` (new, portable — `clearinghouse-sync-powermap`)

A small unit separate from `SyncEngine` (membership management is a distinct concern from
per-row sync). Stateless over the client + the engine's descriptor registry; takes explicit
`session`.

```python
@dataclass(frozen=True)
class DiscoverySpec:
    root_type: str            # "jurisdiction"
    root_id: str              # "usa-wa"
    follow: Sequence[str]     # ["lineage","affiliated_orgs","org_children","roles","assignments","people"]

class SubscriptionReconciler:
    def __init__(self, client, engine: SyncEngine, spec: DiscoverySpec): ...

    async def sync_subscriptions(self, session) -> SubscriptionSyncReport:
        # 1. discovered = client.discover(**spec)        — candidate ids + types
        # 2. registered = client.list_subscriptions()    — what PM holds for this key
        # 3. new_ids    = discovered - registered         — additive diff
        # 4. if new_ids: client.add_subscriptions(new_ids)
        # 5. backfill: for each NEW id, route to a descriptor by entity_type,
        #              fetch_record(client, id), engine.apply_record(...)
        # returns counts: discovered / registered / newly_subscribed / backfilled / not_found
```

- **Additive-only** — never calls `remove_subscriptions`; step 3 is a set difference.
- **Backfill routing** — maps PM `entity_type` → descriptor via `engine.descriptor_for()`,
  then uses the descriptor's `fetch_record` + `engine.apply_record` (the same LWW path the
  feed uses, so backfill and feed are identical in effect — idempotent).
- **Bootstrap vs backstop share this method.** Bootstrap = first call (registered set empty →
  every id is "new" → full backfill). Backstop = later calls (only genuinely-new ids
  backfilled). No separate bootstrap logic beyond the entrypoint that invokes it.
- **Type alignment** — PM emits `organization` / `role_assignment`; planning verifies each
  descriptor's `entity_type` matches PM's discovery/feed strings. Unknown type → log + skip,
  never crash.

`SyncEngine` is otherwise unchanged except for the `after` plumbing in `process_feed`.

### 3. Deployment binding (`usa-wa-sync-powermap`)

**Config (`SidecarSettings`):**

```python
powermap_discovery_root_type: str = "jurisdiction"
powermap_discovery_root_id: str = "usa-wa"
powermap_discovery_follow: list[str] = ["lineage", "affiliated_orgs",
    "org_children", "roles", "assignments", "people"]
subscription_backstop_cadence: timedelta = timedelta(hours=1)
```

The existing `powermap_api_key` is reused. **Prerequisite:** that key must be granted the
`subscriptions:write` scope on the PM side (deploy note, not code).

**Bootstrap entrypoint** — `python -m usa_wa_sync_powermap.bootstrap`: builds client + engine
+ reconciler, opens one session, calls `sync_subscriptions()`, commits, logs the report,
exits. Idempotent; errors propagate (non-zero exit).

**Sidecar wiring (`Sidecar`):**

- Holds a `SubscriptionReconciler` + a `subscriptions` `SyncState` stream tracking
  `last_reconcile_at`.
- `tick()` order: **re-discovery backstop (if due) → `process_feed` → sweep → drain.**
  Discovery runs before the feed so newly-subscribed ids are registered before the feed pull
  that carries their changes.
- Backstop "due" check mirrors the existing `_reconcile_due` pattern, keyed on the
  `subscriptions` stream + `subscription_backstop_cadence`.

**Jurisdiction descriptor change (`descriptors/jurisdiction.py`):**

- `reconcile_enabled = False` — the full-list reconcile is retired. `read_path` stays (still
  used by `fetch_record` for both feed and backfill get-by-id). The scope-note docstring is
  updated to reference the subscription model instead of #10.
- All five descriptors are now feed + discovery driven; no descriptor runs a full-list
  reconcile. `SyncEngine.reconcile()` stays in place (dead for usa-wa, still valid for siblings).

**Cursor cutover** — one-time operational step (documented in the plan): on deploy, reset the
`changes_feed` `SyncState` cursor to NULL so the feed restarts from seq 0 under the new
`after` semantics. Bounded to subscribed entities and idempotent under LWW. Bootstrap is run
once before/at first start.

### 4. `SyncState` cursor storage

Keep the existing `cursor: String(256)` column; store `str(after)`. `process_feed` parses to
`int` on read, writes `str(next_after)`. **No migration needed.** A non-integer stored value
(the stale timestamp at cutover) is treated as NULL/0 with a one-time log rather than a crash.

---

## Error Handling

- **Feed cursor parse** — non-integer stored cursor → treat as NULL/0, log once (cutover
  belt-and-suspenders).
- **Backstop failures** (discovery 5xx, registration blocked) — caught at the cycle boundary
  (existing per-cycle try/rollback in `run_cycle`); logged `subscription_backstop_failed`;
  cycle continues, feed unaffected; next cadence retries.
- **Bootstrap failures** — propagate, non-zero exit, nothing committed (single session);
  operator re-runs.
- **`subscriptions:write` missing** (403 on POST) — surfaces as `DeliveryBlockedError`. In the
  backstop: logged, cycle continues (read path via already-registered subs still works). In
  bootstrap: fails loudly. Documented as the key-scope prerequisite.
- **`not_found` ids** from POST — logged at warning, non-fatal (the rest of the batch registers).
- **Unknown `entity_type`** from discovery/feed — `descriptor_for` returns None → log + skip
  (existing feed behavior).
- **Deletes** — `change_kind="deleted"` still skipped at MVP (unchanged); `entity_changes` is
  now PM's tombstone but the sidecar does not act on it yet.

---

## Testing (TDD, red → green)

- **Client wrapper (`pmclient`)** — `get_changes(after=int)` sends the integer param + parses
  `next_after`; `discover` / `list_subscriptions` paginate; `add_subscriptions` parses the
  result; error mapping (403→blocked, 422→rejected, 5xx→retryable) for the new ops. Against
  the regenerated SDK.
- **`SubscriptionReconciler`** (fake client) — additive diff (only new ids POSTed); backfill
  routes by type + applies via LWW; empty registered set → full backfill (bootstrap case);
  `not_found` / unknown-type tolerated; idempotent re-run is a no-op.
- **`SyncEngine.process_feed`** — integer-cursor round-trip (read `str→int`, write `int→str`);
  non-integer stored cursor → starts from 0.
- **Sidecar** — tick runs backstop before feed; backstop respects cadence (not every cycle);
  backstop failure does not abort the cycle.
- **Fakes** — extend `FakeClient` in `testing.py` with discovery/subscription state so this
  package and siblings can test against it.
- Full `uv run pytest` + `ruff check .` green before ship.

---

## Implementation Order

1. **Regenerate `packages/powermap-client/`** (AGENTS.md procedure) — picks up the `after`
   param + the subscription/discover ops; everything else compiles against it.
2. Client Protocol + value types + `pmclient` dispatch/error-mapping (cursor change + new ops).
3. `SyncEngine.process_feed` `after` plumbing + `SyncState` int-cursor handling.
4. `SubscriptionReconciler` + `DiscoverySpec` + `testing.py` fakes.
5. Deployment binding: config, bootstrap entrypoint, sidecar wiring, jurisdiction descriptor
   `reconcile_enabled=False`.
6. Cutover runbook: reset `changes_feed` cursor; grant `subscriptions:write`; run bootstrap;
   restart the sidecar service.

---

## Prerequisites & Operational Notes

- The `powermap_api_key` must carry the `subscriptions:write` scope (PM-side grant).
- `packages/powermap-client/` regeneration is the first code step.
- Cutover requires resetting the `changes_feed` cursor and a one-time `bootstrap` run.

---

## Out of Scope

- **Pruning / unsubscribe** — additive-only by decision; revisit as a follow-up if stale
  memberships ever matter.
- **Cache eviction** of entities that leave the WA subtree.
- **Acting on `deleted` tombstones** from the feed (still skipped at MVP).
- **Saved-search / auto-enroll** — PM-side v2; the client re-runs discovery instead.
- **Write path** — sweep/observe/outbox unchanged.
