---
title: Heal the chronic LWW assignment-clock ping-pong (#102)
date: 2026-07-19
status: draft
---

## Problem

The PM sync sidecar re-produces **~4,300 unchanged `usa_wa_legislature` assignment
observations to Power Map on every reconcile** — chronic since 2026-07-06 (26,990 deliveries of
the same 4,356 rows over 10 days), now `429`-throttled by PM. Root cause (#102): `apply_record`'s
local-newer branch ([`engine.py:1133`](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/engine.py#L1133))
enqueues an `OP_UPDATE` whenever local `updated_at` > PM's — **with no payload-drift gate** — and
local `updated_at` legitimately drifted ahead of PM on 2026-07-06 (a span backfill deepened/created
those rows). The delivery path ([`_deliver`](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/engine.py#L782))
stamps the anchor + enrich fingerprint but **never adopts PM's clock back**, and PM does not advance
its own `updated_at` on the no-op observation — so local stays permanently "newer" and the reconcile
re-enqueues forever. This blocks #101 PM go-live (resuming would compound the churn with the 284 new
House spans) and wastes ~4,300 PM writes per cycle. Sidecar is currently paused.

## Approach

Two changes, sequenced — **heal the existing skew, then close the mechanism**.

1. **One-shot heal CLI** `heal_assignment_clocks` (sibling of `heal_committee_curation`): for each
   anchored `role_assignment` whose local `updated_at` is strictly newer than PM's, re-fetch PM's
   record (read-only) and adopt PM's `updated_at` onto the local row. Achieves LWW parity → the
   `anchored_cohort` reconcile stops enqueuing those rows → churn stops → #101 unblocks. App-role
   local write; `--dry-run`; idempotent (no-op at parity). Counters: healed / already-parity /
   no-pm-record.

2. **Durable engine gate**: in `apply_record`'s local-newer branch, when the observation we would
   produce is **unchanged from PM's freshly-fetched record**, adopt PM's clock (like the PM-wins
   branch already does via `_adopt_remote_clock`) and **skip the enqueue** instead of pushing an
   identical payload. This makes the reconcile self-healing and prevents recurrence structurally.

Sequence: heal first (immediate, low-risk unblock), gate second (shared-engine, careful CR). The
heal is safe before the gate because re-arming only follows a *genuine* future change (rare — the
daily span rebuild's SQLAlchemy dirty-tracking does **not** bump unchanged rows), and the gate then
eliminates even that residual.

## Tradeoffs / alternatives

- **Heal-only (no gate):** simplest, unblocks #101, but the skew slowly re-accumulates on genuine
  future changes → chronic-churn-lite returns. Rejected as the *sole* fix; kept as step 1.
- **Drain-side clock adoption** (adopt PM's clock on successful delivery so production self-settles):
  the most "correct" root fix, but `ObservationResult` exposes only `raw` (no typed PM timestamp),
  so it depends on PM's observation-response shape or costs a re-fetch per delivery — higher
  coupling/risk. Deferred unless the gate proves insufficient.
- **Dedicated sync-clock column** (stop comparing local write-time in LWW): larger schema/engine
  change, out of scope for a churn fix. The gate achieves the same effect without a migration.

## Steps

1. *(done)* Confirm root cause: un-gated local-newer enqueue; delivery doesn't adopt PM's clock;
   span-emit dirty-tracking means an unchanged re-emit doesn't bump `updated_at` (no daily re-arm).
2. **Heal CLI** `heal_assignment_clocks.py` — TDD. Enumerate anchored assignments, re-fetch PM
   record, adopt PM's `updated_at` where local is newer; `--dry-run`; counts. Unit + integration
   tests (skew heals to parity; parity row is a no-op; missing PM record counted, not crashed).
3. **Engine gate** — TDD. In `apply_record`, gate the local-newer `OP_UPDATE` on real drift: if the
   `to_observation(local)` payload matches PM's `record`, adopt PM's clock + skip enqueue. Tests:
   identical payload → no enqueue + clock adopted; genuine field change → still enqueues.
4. **Run the heal on prod** (dry-run → real), then a manual `anchored_cohort` reconcile pass →
   verify it enqueues ~0 assignment UPDATEs (outbox PENDING stabilizes near 0).
5. **Deploy the engine gate**; resume the sidecar; confirm the residual backlog drains-and-converges
   and the #101 House spans produce (161 UPDATE via transferred anchors, 123 CREATE).
6. Verify + close #102; then run #101 go-live verification and close #101.

## Open questions / risks

- **Sequencing (for you):** heal-then-gate (fastest #101 unblock) vs gate-then-heal (no transient
  re-arm window)? **Recommend heal-first.**
- **Gate's drift check (step 3 design):** direct compare of `to_observation(local)` vs the fetched
  PM `record` (no new storage — preferred), vs a base-observation fingerprint stamped on delivery
  (parallel to the enrich fingerprint). Settle with tests; the direct compare is leaner.
- **Shared-engine blast radius:** the gate lives in `clearinghouse-sync-powermap` (sibling-reusable).
  Keep it behind the existing descriptor contract; full suite + CR before deploy.
- **Heal role:** the local `updated_at` write is app-role DML (assignments are not provenance);
  confirm no owner role needed.
- **Scope:** the heal covers #101's freshly-built House rows too (their local clock is today, ahead
  of PM) — so #101 settles as part of the same heal.
