---
title: Re-enrich already-anchored rows when held identifiers / carry-fields change (#34)
date: 2026-06-21
status: draft
---

# Re-enrich already-anchored rows (#34)

## Problem

Enrich-on-match only fires for **un-anchored** rows ([`_sweep_row`](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/engine.py)). Once a row is anchored, nothing re-evaluates `needs_enrich`, so when the data we hold for an already-anchored entity changes it never propagates to PM on its own. Two coupled gaps: (1) **trigger** — `_reconcile_anchored_cohort` re-fetches our anchored rows but never calls `needs_enrich`/enqueues an enrich; (2) **detection** — `needs_enrich` only checks identifier presence (`record_has_identifier`), not whether the carry fields we hold (acronym, contact_methods, names) are present/current. The only remedy today is the manual, hand-widened `backfill_contact_labels` CLI. This surfaced concretely in #33 (legislature anchor-type switch — trigger gap) and #31 (acronym object-shape fix — detection gap).

## Approach

Two phases, shipped separately.

**Phase 1 — trigger.** In `_reconcile_anchored_cohort`, after `apply_record`, mirror what `_sweep_row` already does: if `descriptor.enrich_identifier_type and await descriptor.needs_enrich(record, row)` then `_enqueue(OP_ENRICH)`. Factor the two-line check into a shared `_maybe_enqueue_enrich(...)` helper used by both sweep and reconcile. This closes the #33-class case (identifier *type* changed → `record_has_identifier` False against the new id_type → enrich). Loop-safe by construction: enrich delivers → PM `updated_at` advances → next reconcile's `apply_record` adopts the remote clock (PM newer) → no spurious UPDATE; `needs_enrich` now False; the `_enqueue` blocking-status guard dedups before delivery. Converges in one round.

**Phase 2 — detection via local fingerprint (not PM-record comparison).** Add a separate `needs_reenrich` path that re-enriches when **our** enrich payload changed since we last pushed it — purely local, loop-free. Hash the `to_enrich_observation` carry payload; persist the last-delivered hash per row in a new sync-schema table keyed `(entity_type, local_id)` (alongside `OutboxEntry`/`SyncState`, keeping the portable layer jurisdiction-agnostic — one migration). Re-enrich when current hash ≠ stored hash; update the stored hash after delivery settles. Keep the *identifier* check on the PM-read path (Phase 1) — it genuinely must exist on PM — and put only carry-fields on the fingerprint. After Phase 2, `backfill_contact_labels` becomes a force-push convenience rather than the only recovery path.

## Tradeoffs / alternatives

- **Broaden `needs_enrich` to diff carry-fields against the PM record (the issue's literal proposed direction)** — rejected as the Phase 2 mechanism because diffing our evidence against PM's *curated* record reintroduces the LWW write-back hazard the issue itself flags: PM curates `is_canonical`/name order/dedups, so a subset/equality comparator risks perpetual false-positive enrich, and if PM legitimately curates away a duplicate we asserted, "PM is missing our evidence" stays True **forever** → re-enrich every cycle (the 403-loop pattern reborn). The local fingerprint fires **once** per our-side change and stays quiet even if PM curates the evidence away.
- **Overload `needs_enrich` to also carry the fingerprint logic** — rejected: would change the un-anchored happy-path semantics (#29). Keep `needs_enrich` (identifier-presence, PM-read) and add a separate `needs_reenrich` (carry-field, local fingerprint).
- **Put the enrich check inside `apply_record` (covers feed + reconcile + siblings in one place)** — deferred, not chosen for Phase 1: more general but riskier (touches the feed path, sibling descriptors, and the KEPT_LOCAL/UPDATE branches). The reconcile-loop placement matches the issue's primary ask and is the conservative cohort-bounded scope. Revisit if a feed-driven self-heal is needed.
- **Store the fingerprint as a column on each producer model** — rejected in favor of a sync-schema table: a per-model column leaks sync bookkeeping into every domain model and repeats the migration per entity; one keyed table keeps it in the portable sync layer.
- **Keep only the manual `backfill_contact_labels` CLI** — rejected: it must be hand-widened for every new carry field (#21/#25 blind spot) and is the stated thing #34 wants to retire.

## Steps

### Phase 1 — trigger (ship first; closes #33)

1. **(RED)** Add an engine test: an anchored org whose PM record lacks the held identifier → `reconcile` (anchored_cohort) enqueues exactly one `OP_ENRICH`; an anchored org whose PM record already holds it → no enqueue; re-running the reconcile while the enrich is still PENDING → still exactly one (dedup). Use the shipped `FakeClient`/`FakeDescriptor` doubles where possible.
2. **(GREEN)** Extract `_maybe_enqueue_enrich(session, descriptor, record, row)` from `_sweep_row`; call it in both `_sweep_row` and the `_reconcile_anchored_cohort` per-row loop after `apply_record`. Add a one-line note to the reconcile docstring that a deploy-time mass re-enrich is bounded by keyset paging + per-page commit + `_enqueue` dedup + outbox backoff.
3. **(REFACTOR)** Confirm convergence/loop-safety with a two-cycle test (enrich delivered → PM clock adopted → second reconcile enqueues nothing). `uv run pytest` + `uv run ruff check .` green.
4. Commit Phase 1 (`#34 feat: re-enrich anchored rows on identifier change`).

### Phase 2 — detection via fingerprint (closes #31-class)

5. **(RED)** Test the carry-payload hash: stable input → stable hash; a changed acronym shape / added carry field / added `contact_methods` label → changed hash. Pure-function test, no DB.
6. Add a sync-schema model `EnrichFingerprint` (`entity_type`, `local_id`, `payload_hash`, timestamps; unique on `(entity_type, local_id)`) in `clearinghouse_sync_powermap/models.py`; autogenerate the alembic migration; add the schema to `scripts/grants.sql` if it introduces a new schema.
7. **(RED→GREEN)** Add `needs_reenrich(session, descriptor, row)` (or an engine helper): compute current `to_enrich_observation` hash, compare to the stored fingerprint, return True on mismatch (or no stored row). Wire it into `_maybe_enqueue_enrich` alongside the identifier check (enqueue `OP_ENRICH` if either fires). Test: changed carry shape on an anchored row → enrich enqueued.
8. **(GREEN)** On enrich delivery settle (in the outbox worker / `_deliver` success path for `OP_ENRICH`), upsert the fingerprint to the delivered payload hash. Test: after delivery, the same reconcile no longer enqueues (fingerprint matches); a subsequent carry change re-fires once.
9. **(REFACTOR)** Update `backfill_contact_labels` docstring to note it is now a force-push convenience; update `AGENTS.md`/`docs/COMMANDS.md` if the operator story changes. `uv run pytest` + `ruff` green.
10. Commit Phase 2 (`#34 feat: fingerprint-based carry-field re-enrich`), then run the migrate oneshot + restart the sidecar per the runbook.

## Open questions / risks

- **Fingerprint hash stability** — `to_enrich_observation` must serialize deterministically (sorted keys, stable list order) before hashing, or unrelated reorderings cause false re-enrich. Confirm/normalize the serialization in step 5.
- **Delivery-settle hook for fingerprint write** — step 8 needs the outbox worker to know the delivered payload's hash at settle time. Decide: recompute at settle, or stamp the hash onto the `OutboxEntry` at enqueue and copy it on success. Stamping at enqueue is more robust (the row may have changed again between enqueue and delivery).
- **Mass re-enrich on deploy** — a carry-shape code change re-fingerprints the whole anchored cohort at once. Throttled through the outbox, but worth confirming the drain backoff is comfortable with a one-time cohort-sized burst (orgs ~tens today; fine).
- **Phase 1 standalone value** — Phase 1 closes #33 and is independently shippable; confirm we want to land it before Phase 2 rather than as one PR.
