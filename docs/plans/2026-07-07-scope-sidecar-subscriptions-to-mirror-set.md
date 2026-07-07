# Scope sidecar subscriptions to the mirror set (#73 Axis 1)

Status: **steps 1–6 implemented.** Steps 1–5 stop the bleed (new syncs no longer subscribe
strangers); step 6 (`prune_subscriptions` CLI) reclaims the existing inert strangers. Axis 2
(cadence) shipped earlier: commit `#73 feat: retune sidecar reconcile + re-discovery cadence`.

## Implemented (steps 1–5)

- `SubscriptionReconciler(..., include_local_cohort=True)` — a new `_discover_local_cohort`
  enumerates OUR anchored producer rows (any `reconcile_enabled` descriptor; keyset-paged,
  skips tombstoned rows, keeps archived) as `DiscoveredEntity` candidates, deduped against
  PM discovery by `entity_id`. Portable + off-by-default (siblings unaffected).
- `SyncEngine.descriptors` read-only accessor so the reconciler can enumerate the entity set.
- `powermap_discovery_follow` default narrowed to `["lineage"]` (jurisdiction cache only).
- `registry.build_reconciler(client, engine, settings)` wires the flag on; bootstrap +
  `__main__` both route through it so they agree on membership policy.
- Observability: the existing `subscription_sync` log already emits `discovered` +
  `backfill_skipped`; with the mirror-set scope `discovered` ≈ the mirror set and
  `backfill_skipped` trends to ~0 (strangers no longer surfaced).

**Step 6 (reclaim) implemented** — `SubscriptionReconciler.prune_subscriptions` diffs PM's
registered set against the freshly-discovered mirror set and `remove_subscriptions` the
difference, guarded by an empty-desired-set abort + a `--max-prune-fraction` floor (default
0.9, permissive because the first run legitimately removes ~half). Surfaced as the one-shot
`python -m usa_wa_sync_powermap.prune_subscriptions` CLI (`--dry-run`; exit 0/2/3; no operator
token). Idempotent — a second run finds nothing stale. **Run once** after this lands to
reclaim the ~1,000 existing strangers; not a timer (the narrowed discovery keeps the set
clean going forward).

## Original plan follows

## Problem

The sidecar subscribes to the **entire** WA subtree — ~2,284 PM entities — but every
producer read descriptor (org/role/person/assignment) is update-only, anchor-keyed:
`local_match` returns `None` (skip) for anything usa-wa never produced. So ~325 people /
~385 roles / ~332 assignments are subscribed, delivered by the feed, fetched, and
**discarded**.

`build_discovery_spec` roots at the `usa-wa` jurisdiction and follows
`lineage → affiliated_orgs → org_children → roles → assignments → people` — a PM-side
graph walk. The feed apply path fetches every changed subscribed entity *before* reaching
the skip ([engine.py:1231](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/engine.py#L1231)),
so each PM-side change to a stranger = a wasted fetch (person = record + `/events`) then
discard. Cost scales with the historical committee backfill (sub-project 3, #72): every
backfilled committee drags its `org_children → roles → assignments → people` into the
subscription set across 30+ years.

Architectural mismatch: **discover-everything subscription vs mirror-only-ours descriptors.**
No correctness impact (skips are correct) — pure waste that scales badly.

## Approach

Subscribe to exactly what we mirror, split by who authors the data:

- **Producers (org/role/person/assignment)** — usa-wa *originates* these (WSL adapter →
  outbox → PM → anchored locally). The subscription set is our **local anchored cohort**:
  `{ descriptor.anchor_value(row) for row in descriptor.model where anchor IS NOT NULL }`.
  This is the identical row set the anchored-cohort reconcile already walks
  ([engine.py:1099](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/engine.py#L1099)),
  so it needs no PM graph walk — we subscribe to our own rows to receive PM's curation/
  merge/enrich feed events for them, and nothing else.
- **Jurisdictions** — mirror-only, PM-authoritative (usa-wa produces zero). These still
  need PM discovery to populate a cold cache. Keep the PM walk but narrow `follow` to
  `lineage` only (drop `affiliated_orgs`/`org_children`/`roles`/`assignments`/`people` —
  those edges only exist to reach the strangers we're eliminating).

Net: strangers never enter the subscription set. Our produced rows keep real-time feed
delivery. The jurisdiction cache still bootstraps from PM.

**Amplifier containment (folds in Option 4 / #72).** Because producer subscriptions come
from the anchored cohort, dissolved historical committees only get subscribed if we
actually anchored their roles/assignments/people — and #72 already scopes the member
fan-out to current-biennium active committees, so those strangers are never produced.
Optionally further scope the producer cohort to live + active (exclude archived/deleted +
`Organization.active == False`) so dead historical orgs don't hold live subscriptions.

## Tradeoffs

- **Cold-start ordering.** A producer row must be anchored (pm id stored) before it can be
  subscribed. That already holds: the outbox writes to PM and stores the anchor before the
  row is "ours." A newly-produced row is picked up by the next backstop (now 6h, #73 Axis
  2) — acceptable; the outbox write is the authoritative path, subscription is only for
  *inbound* PM edits.
- **Pruning.** The current reconciler is additive-only (never unsubscribes, CannObserv/
  usa-wa#10). Switching the producer source to "current anchored cohort" makes the *desired*
  set shrink when a row is deleted — but we can stay additive initially (subscribe the
  cohort, still never remove) and defer active unsubscribe of the ~1,000 existing strangers
  to a follow-up using `remove_subscriptions` (the API supports it). Decide below.
- **Loses the PM-subtree "discover new WA orgs we didn't produce" path.** That path only
  ever surfaced strangers we skip, so no real loss — but confirm no future descriptor is
  meant to mirror a PM-authored org.

## Steps

1. **Test-first:** `SubscriptionReconciler` (or a new local-cohort discovery source) yields
   exactly the anchored producer ids + the jurisdiction lineage set, given a seeded local
   cache + fake client. Assert strangers in the fake PM subtree are NOT subscribed.
2. Add a `discover_local_cohort(session)` producing `DiscoveredEntity(entity_type, anchor)`
   over the anchored producer descriptors (reuse `anchor_column_expr`/`anchor_value`,
   keyset-paged like the reconcile). Optional live+active scope.
3. Narrow `powermap_discovery_follow` default to `["lineage"]` (jurisdiction cache only);
   keep it env-overridable.
4. Wire `sync_subscriptions` to union (jurisdiction PM-discovery) ∪ (local producer cohort).
   Keep additive registration; keep the backfill-new-by-id step.
5. Add observability: log discovered-vs-mirrored delta (the `SubscriptionSyncReport`
   `backfill_skipped` stranger counter should trend to ~0).
6. **Decide pruning (open question).** If we actively unsubscribe: a one-shot
   `prune_subscriptions` CLI diffing PM's `list_subscriptions` against the mirror set and
   calling `remove_subscriptions`, guarded like the reconcilers (empty-set / max-fraction
   abort). Otherwise document that the ~1,000 existing strangers stay subscribed-but-inert
   and only new drift is prevented.
7. Full sidecar suite + `test_sidecar` backstop tests green; ruff clean.

## Open questions

- **Prune or leave inert?** ~~open~~ **Resolved:** both — steps 1–5 stop the bleed, step 6's
  guarded `prune_subscriptions` CLI reclaims the existing waste (empty-desired + max-fraction
  aborts against a discovery collapse). Prune is a run-once manual CLI, not a timer.
- **live+active cohort scope** — worth it now, or after the historical backfill lands and the
  stranger count is measured? Recommend gating on the observability from step 5.
- Any planned descriptor that mirrors a **PM-authored** (non-produced) org? If so it needs a
  PM-discovery source and this split must accommodate it.
