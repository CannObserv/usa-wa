---
title: Adapt the PM sync sidecar to Power Map's discovery/subscription model
date: 2026-06-15
status: done
---

# Adapt the PM sync sidecar to Power Map's discovery/subscription model

Design: [docs/specs/2026-06-15-pm-subscription-sync-design.md](../specs/2026-06-15-pm-subscription-sync-design.md) · Issue: CannObserv/usa-wa#10 · Upstream: power-map#203 (shipped)

## Problem

Power Map shipped a clean-break per-API-key subscription model (power-map#203, verified live).
`GET /api/v1/changes` now requires `?after=<int seq_id>` instead of `?since=<timestamp>`, and
the feed is always subscription-filtered — an empty subscription set returns an empty feed. Our
`GeneratedPowerMapClient.get_changes` still sends a `since` datetime, so the sidecar read path is
broken against live PM. The jurisdiction full-list reconcile remains unfiltered and still
firehoses all 50 states (the original #10 complaint). The sidecar must register subscriptions
(via the new discovery endpoint), consume the integer-cursor feed, and backfill current state by
id.

## Approach

Adopt the design's Approach A: a portable subscription/discovery mechanism in
`clearinghouse-sync-powermap`, WA-specific params + bootstrap + wiring in `usa-wa-sync-powermap`.
Read path becomes **discovery (membership) → subscription-filtered feed (changes) → fetch-by-id
(backfill)**, all WA-scoped. Lifecycle is hybrid: a one-shot `bootstrap` command plus an
additive-only periodic re-discovery backstop in the sidecar loop. The jurisdiction full-list
reconcile is retired. Write path is untouched. TDD throughout (red → green), bottom-up so each
layer compiles against the one below.

## Tradeoffs / alternatives

Alternatives were explored during brainstorming (see the design doc's "Approved Decisions"):

- **All subscription logic in the deployment package** — rejected: breaks the sibling-reusable
  sync-engine boundary; the next jurisdiction re-implements it.
- **Minimal inline registration in `Sidecar.tick`** — rejected: tangles discovery/backfill/feed
  into one method, hard to unit-test.
- **One-shot bootstrap only (no backstop)** / **periodic only (no bootstrap)** — rejected in
  favor of hybrid: bootstrap gives a clean initial population, the backstop catches graph drift.
- **Pruning / cache eviction** — deferred (additive-only); avoids eviction-correctness surface.

## Steps

1. **Regenerate `packages/powermap-client/`** per the AGENTS.md procedure. Verify the diff adds
   the `after` param to the change-feed op and the `subscriptions` / `discover` operations +
   models; `uv sync`. (Verifiable: regenerated ops importable; existing `pmclient` tests still
   collect.)
2. **Client Protocol + value types** (`clearinghouse-sync-powermap/client.py`): change
   `get_changes(since)` → `get_changes(after: int | None)`, rename `ChangePage.cursor` →
   `next_after: int | None`, add `DiscoveredEntity` / `SubscriptionResult` value types and the
   `discover` / `list_subscriptions` / `add_subscriptions` / `remove_subscriptions` Protocol
   methods. (Verifiable: red→green unit expectations on the Protocol shape; type-checks.)
3. **`GeneratedPowerMapClient` dispatch** (`pmclient.py`): implement the new ops over the
   regenerated SDK with internal pagination for `discover`/`list_subscriptions`; route errors
   through `_send` (403→blocked, 422→rejected, 5xx→retryable); delete `_EPOCH` + `since` parsing.
   (Verifiable: wrapper tests for `after` round-trip, pagination, error mapping — green.)
4. **`SyncEngine.process_feed` + cursor storage**: read the `changes_feed` `SyncState.cursor` as
   `int` (non-integer → 0, log once), pass `after`, write `str(next_after)`. (Verifiable: feed
   tests for int round-trip and stale-cursor fallback — green.)
5. **`SubscriptionReconciler` + `DiscoverySpec`** (new module in `clearinghouse-sync-powermap`)
   + extend `testing.py` `FakeClient` with discovery/subscription state. Implement
   `sync_subscriptions`: discover → diff vs registered → `add_subscriptions(new)` → backfill new
   ids by type via `descriptor_for` + `fetch_record` + `apply_record`; additive-only; tolerate
   `not_found` / unknown type. (Verifiable: reconciler tests — additive diff, full backfill on
   empty registered set, idempotent re-run, tolerance — green.)
6. **Deployment binding** (`usa-wa-sync-powermap`): add discovery + backstop-cadence fields to
   `SidecarSettings`; add the `bootstrap` entrypoint; wire the reconciler + `subscriptions`
   `SyncState` stream into `Sidecar` with tick order backstop→feed→sweep→drain and a cadence-gated
   "due" check; set `JurisdictionDescriptor.reconcile_enabled = False` and refresh its docstring.
   (Verifiable: sidecar tests — backstop-before-feed, cadence respected, backstop failure doesn't
   abort the cycle; bootstrap entrypoint test — green.)
7. **Full gate + cutover runbook**: `uv run pytest` and `uv run ruff check .` green; document the
   cutover in this plan / deploy notes — grant `subscriptions:write` on the PM key, reset the
   `changes_feed` cursor to NULL, run `python -m usa_wa_sync_powermap.bootstrap`, restart
   `usa-wa-sync-powermap`. (Verifiable: suite green; runbook present.)

## Cutover runbook

Code is merged behind the new contract; the read path stays dark until the key is
scoped and the subscription set is bootstrapped. On deploy:

1. **Grant the key `subscriptions:write`** on the PM side (the `POWERMAP_API_KEY`
   already used for observations). Without it, bootstrap fails loudly and the
   in-loop backstop logs `subscription_register_*` failures while the feed still
   serves any already-registered subs.
2. **Reset the feed cursor** (optional — belt-and-suspenders). The engine's
   `_parse_after` already treats a leftover pre-#203 timestamp cursor as "from the
   start" (seq 0) with a one-time `powermap_feed_cursor_reset` log, so this only
   suppresses that warning:
   `UPDATE sync.powermap_sync_state SET cursor = NULL WHERE stream = 'changes_feed';`
3. **Bootstrap the subscription set + cache:**
   `export $(cat /etc/usa-wa/.env .env | xargs) && uv run python -m usa_wa_sync_powermap.bootstrap`
   (idempotent — safe to re-run; logs `bootstrap_complete` with the counts).
4. **Restart the sidecar service:** `sudo systemctl restart usa-wa-sync-powermap`.
   The in-loop backstop then re-runs discovery every
   `subscription_backstop_cadence` (default 1h) to catch graph drift.

## Open questions / risks

- **`subscriptions:write` scope grant is a hard prerequisite** — without it the POST 403s; the
  backstop degrades gracefully (logs, feed via existing subs still works) but bootstrap fails
  loudly. The grant is PM-side and outside this repo; confirm it's in place before cutover.
- **Generated-client drift** — if PM's OpenAPI names operations/models differently than assumed,
  step 3's dispatch is where it surfaces; step 1's diff review is the early-warning.
- **`entity_type` string alignment** — descriptors must match PM's `organization` /
  `role_assignment` discovery/feed strings; verified in step 5, with unknown-type skip as the
  safety net.
- **Cutover replay** — resetting the cursor to NULL replays ≤90 days of WA-subtree change rows
  from seq 0; bounded and idempotent under LWW, but expect a heavier first feed cycle.
