# PM Sync Sidecar — Backlog Clearance Orchestration Plan

**Date:** 2026-06-16
**Tracking issue:** CannObserv/usa-wa#17
**Scope:** GitHub issues #6, #7, #8, #9, #12, #13, #15, #16
**Deferred:** #14 (blocked on the legislature adapter)
**Context:** all eight are carve-outs from the #4 / #10 shipping cycle's CR rounds — a
followup-derived backlog, so contested files are already named in the issue bodies.

---

## Goal

Clear the post-#10 hardening backlog for the Power Map sync sidecar with a parallel-safe,
merge-in-any-order batch plan. Every issue was re-validated against the subscription
mechanism that #10 shipped (closed 2026-06-16) before entering scope — two were re-pointed,
one rescoped, one deferred.

## Approved approach

- **Rubric:** standard — `Score = (Foundation × 2) + (Correctness × 2) + Scope`, max 15.
- **Deployment context:** pre-production (runway to build it right; identity write paths still
  dormant — no adapter writes canonical identity rows yet).
- **Parallelism:** hybrid — parallel within a batch on file-disjoint work, merge gate between
  batches. Worktree isolation for every worker.
- **Worktree ceiling = 3.** Plain `git worktree` (no per-worktree DB clone / port pool). The
  binding constraint is the **shared Postgres test DB** (`TEST_DATABASE_URL`): concurrent
  `uv run pytest` runs from separate worktrees contend on schema bootstrap. Mitigation —
  workers run targeted package tests (`--no-cov <pkg>/tests`); the orchestrator runs the full
  suite once on the batch branch.
- **Batch→main merge:** regular merge commit (preserves per-agent history). Intra-batch
  worker→batch integration is FF/regular-merge (never squash/rebase — preserves the ancestor
  link the destroy script checks).

---

## Critical validity analysis vs. #10 (the gate that shaped scope)

#10 (closed 2026-06-16) replaced the firehose changes-feed with a **per-API-key,
server-side subscription model**:

- `/changes` now takes an integer `after` cursor (not `since` timestamp) and is **always
  subscription-filtered** — empty subs ⇒ empty feed, no firehose fallback.
- **Full-list reconcile retired for usa-wa** — all five descriptors set
  `reconcile_enabled = False`. `SyncEngine.reconcile()` survives only as sibling-reusable code.
- New `SubscriptionReconciler` — additive-only discover → register → **backfill-by-id for new
  ids only**. Ongoing curation of already-anchored entities flows through the
  subscription-filtered **feed**.
- New `pmclient` ops `discover` / `list_subscriptions` each added a **fresh `while True`
  pagination loop** — and these are the **live** read loops now.

| # | Verdict vs. #10 | Disposition |
|---|---|---|
| 6 | Valid but mis-pointed — its named target (`reconcile`'s `while True`) is now dead for usa-wa, while #10 added two **live** unbounded loops (`discover`/`list_subscriptions`). Risk class is *more* present, relocated. | **Keep + reframe** to the live loops; reconcile guard kept as sibling defensive. |
| 7 | Write path, untouched by #10. Still dormant. | Keep as-is. |
| 8 | Write path, untouched by #10. | Keep as-is. |
| 9 | Independent; PM SDK `ObservationEventItem` shape regenerated under #10 — strengthens the case. | Keep as-is. |
| 12 | Match-cascade cap is separate from the subscription feed filter; untouched. | Keep as-is. |
| 13 | **Largely superseded** — the "reconcile-every-non-`none`" framing and `read_source` overload are gone. Residual gap shrinks to: additive backstop backfills only new ids, so a **dropped feed event** for an already-anchored row never re-syncs. | **Rescope down**, keep in backlog. |
| 14 | #10 blocker cleared, but real prerequisite (legislature adapter writing identity rows) still absent → unexecutable. | **Defer** (out of execution scope). |
| 15 | Outbox `_deliver`; parent #5 closed (UNAVAILABLE + dead-letter shipped); untouched. | Keep as-is. |
| 16 | Wraps shipped `redrive_unavailable`; #10 added an HTTP health surface (a mount point). | Keep as-is. |

---

## Prioritization rubrics

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| **Foundation Leverage** | Standalone | 1–2 other issues benefit | Multiple depend on / simplified by it |
| **Correctness Risk** | Cosmetic / organizational | Edge-case incorrect / runtime-failure risk | Data loss, races, **silent failures** |
| **Scope Clarity** | Needs design discovery | Clear direction, minor decisions | Mechanical |

`Score = (Foundation × 2) + (Correctness × 2) + Scope`. Blast radius drives sequencing, not score.

## Scored backlog

| # | Issue | F | C | S | **Score** | Blast |
|---|---|:-:|:-:|:-:|:-:|:-:|
| 8 | scope outbox delivery txn boundary | 2 | 2 | 2 | **10** | Med |
| 15 | deps-not-ready forever-defer invisible (silent stuck path) | 1 | 3 | 2 | **10** | Low |
| 6 | bound pagination loop *(reframed to live discover/list loops)* | 1 | 2 | 3 | **9** | Low |
| 12 | configurable search/match pagination | 1 | 2 | 3 | **9** | Med |
| 7 | batch sweep_unanchored | 1 | 2 | 2 | **8** | Low |
| 9 | refine entity_events → ObservationEventItem | 1 | 1 | 2 | **6** | Med |
| 13 | anchored-cohort re-fetch *(rescoped to dropped-event recovery)* | 1 | 1 | 1 | **5** | Med-High |
| 16 | re-drive surface for UNAVAILABLE | 1 | 1 | 1 | **5** | Low |

---

## Conflict zones (contested files + required ordering)

| File | Issues | Ordering |
|---|---|---|
| `clearinghouse_sync_powermap/engine.py` | #7 `sweep_unanchored`, #8 + #15 `_deliver`/`drain_outbox`, #13 `reconcile` | #8→#15 same method (one agent); reconcile region disjoint from sweep/deliver |
| `clearinghouse_sync_powermap/pmclient.py` | #6 `discover`/`list_subscriptions`, #12 `search_entities` | distinct methods; one owner per batch |
| usa-wa org+person descriptors | #9 event sub-resource, #12 match-cap, #13 `reconcile_mode` | one editor per batch |
| `config.py` (SidecarSettings) | #8 chunk size, #12 search cap, #13 cadence | one editor per batch |
| `sidecar.py` | #8 daemon loop, #13 reconcile-due | one editor per batch |

## Dependency graph

```
#8 ──▶ #15            same _deliver region → one agent, #8 commits first
#9                    independent (migration + domain model)
#16                   independent (API/CLI over shipped redrive_unavailable)
#6                    pmclient loops; reconcile one-liner folded into #13
#12                   pmclient search + descriptor match-cap
#13                   reconcile regime; co-edits config/descriptors with #6,#12 → sequenced last
```

All edges are **file-coauthorship**, not data flow — no cross-issue logic dependency. Sequencing
exists to guarantee any-order merges, not for correctness.

---

## Batch execution plan

### Batch A — 3 parallel agents (start immediately)

| Agent | Issues | Files owned |
|---|---|---|
| A1 | #7 + #8 + #15 (Shape A, sequential commits #8→#15→#7) | `engine.py` (`sweep_unanchored`, `_deliver`, `drain_outbox`), `sidecar.py`, `models.py`, `config.py` |
| A2 | #9 | alembic migration, `clearinghouse-domain-legislative` models, org+person descriptor **event** sub-resource |
| A3 | #16 | `usa-wa-api` main.py + new router, **new** CLI module |

Disjoint: engine/sidecar/config/models → A1; migration/domain/descriptor-events → A2; api → A3.
Merge in any order.

### Batch B — 1 agent (after A merged)

| Agent | Issues | Files owned |
|---|---|---|
| B1 | #6 + #12 (Shape A) | `pmclient.py` (`discover`/`list_subscriptions` bounds + `search_entities` pagination), `config.py` (search cap), org+person descriptor **match-cap** |

### Batch C — 1 agent (after B merged)

| Agent | Issues | Files owned |
|---|---|---|
| C1 | #13 (rescoped) | `engine.py` (`reconcile` region + the bound from #6's one-liner), org+person+role+assignment descriptor `reconcile_mode`, `sidecar.py`, `config.py` (cadence) |

---

## Key decisions

1. **#6 reframed, not retired.** Its original target (`reconcile`'s `while True`) is dead code
   for usa-wa post-#10, but #10 introduced two *live* unbounded loops (`discover`,
   `list_subscriptions`) run every cycle by the discovery backstop. #6's deliverable is bounding
   those, plus a defensive max-page guard on `reconcile` for siblings. That guard line is folded
   into **C1/#13**, which rewrites the reconcile region anyway, to avoid two agents editing it.
2. **#13 rescoped to dropped-event recovery.** #10 made `read_source` no longer overloaded and
   retired full-list reconcile, collapsing #13's option 3. The residual real gap: the additive
   backstop backfills only *new* ids, so a dropped feed event for an already-anchored row never
   re-syncs. C1 implements a bounded anchored-cohort re-fetch (O(our cohort)) for that case.
3. **#8 leads #15 in one agent.** #15's deferred-too-long visibility lives inside the same
   `_deliver` that #8 re-scopes the transaction boundary around — bundling avoids a same-method
   merge conflict and gives one reviewer the whole outbox-delivery story (Shape A).
4. **#16 stays out of `sidecar.py`/`config.py`.** A1 owns both in Batch A. #16 reuses the
   existing `usa-wa-api` auth dependency and puts its CLI in a **new** module so A3 is fully
   file-disjoint from A1.
5. **B and C are single-agent, sequenced.** #6+#12, #12, and #13 pairwise co-edit `config.py`
   and the org/person descriptor classes. Sequencing (B lands pmclient/match-cap, then C edits
   reconcile on top) is merge-safe without relying on second-merge conflict resolution, and
   gives the design-heavy #13 a solo review.
6. **Ceiling = 3, gated by the shared test DB**, not git. Workers run targeted package tests;
   the orchestrator runs the full `uv run pytest` + `ruff check .` on each batch branch before
   requesting review.

## Deferred items

- **#14 — verify identity sync end-to-end.** Real prerequisite (WA legislature adapter writing
  `canonical.*` identity rows) does not exist yet; #10 cleared only the feed-filtering blocker.
  Its verification checklist has also *expanded* to cover the new bootstrap/discovery/backfill
  path. Revisit when the adapter lands.

## Out of scope

- **#13 pruning / unsubscribe / cache eviction** — #10 is additive-only by decision; not part of
  the dropped-event-recovery rescope.
- **Acting on `deleted` feed tombstones** — still skipped at MVP (unchanged by this backlog).
- **Saved-search / PM-side auto-enroll** — PM v2 concern; the client re-runs discovery instead.
