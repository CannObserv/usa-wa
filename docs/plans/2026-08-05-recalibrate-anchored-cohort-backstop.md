---
title: Recalibrate the anchored-cohort backstop to rewind-and-replay (usa-wa#159)
date: 2026-08-05
status: approved
---

# Recalibrate the anchored-cohort backstop (usa-wa#159)

## Problem

The sidecar re-fetches our **entire** produced cohort by id every 12h
(`_reconcile_anchored_cohort`, #13/#73) as a safety net against changes the live
feed silently missed — O(cohort) full-record reads (each person also pulls
`/events`), ~99.9% no-ops. PM#387 now documents what the feed can actually lose:
it is **monotonic but not gapless** and **at-least-once**, the real hazard being
the **concurrent-commit skip** (a higher `seq` commits before a lower one, so an
incremental consumer advances past a row that lands later). Retention is
**90 days** by `changed_at`; horizon fall-off is now detectable via `meta.min_seq`
(#388). The dropped-event class the O(cohort) scan defends against is therefore
almost entirely re-coverable by a cheap **rewind-and-replay** of the feed, leaving
only a small, enumerable residual the scan must still cover. Our own mid-read
crash is already covered — the feed applies a page and advances the cursor in one
transaction — so the scan is over-provisioned for its real job.

## Approach

Add a second, trailing feed consumer: each backstop cycle, **replay the changes
feed from `high_water − margin`** through the existing `apply_record` path
(idempotent under LWW), instead of re-fetching every anchored row. The rewind
re-covers the concurrent-commit skip and any dropped feed event within the margin.
Keep a **low-frequency full-cohort reconcile** (the current scan, cadence widened)
to cover only the documented residual PM's triggers cannot emit: tombstone-bypass
hard deletes, trigger-off bulk paths (`TRUNCATE`/`replica`/`DISABLE TRIGGER`/`COPY`),
and telemetry-only writes. Surface `meta.min_seq` and **alert + fall back to a full
scan** if we ever fell off the 90-day window. Ship **detection-first** (Phase A):
run the replay in shadow and log the would-heal delta vs. the current scan for one
review period before widening the scan cadence (Phase B) — matching the project's
#112/#107 detection-then-act pattern, so we cut the scan only against measured
evidence, not the assumption that replay is sufficient.

## Tradeoffs / alternatives

- **Remove the cohort scan outright, replay only** — rejected: PM#387 enumerates a
  real no-emit residual (tombstone-bypass deletes, trigger-off bulk, telemetry)
  that no feed replay can catch. Right-size, don't remove.
- **Keep the O(cohort) scan, just add conditional GET (#160)** — rejected as the
  *primary* fix: #160 cuts bandwidth/DB/CPU but **not request count** (PM#385
  analysis), so it does not address the scan's read-volume cost. Complementary, not
  a substitute; folded into whichever fetch path survives.
- **Time-based rewind (`changed_at >= T`)** — rejected: PM's cursor is a `seq`
  offset, not a timestamp (`next_since` was retired at PM#203); `get_changes` takes
  `after=<seq>`. Margin is expressed in seq/lag, anchored to `meta.min_seq` for
  horizon safety, not wall-clock.
- **Direct cut-over (no shadow phase)** — rejected: replay sufficiency is an
  empirical claim about our specific cohort/churn; measuring the would-heal delta
  first is cheap insurance against silently under-covering.

## Steps

1. **Surface `meta.min_seq` through the client.** Add `min_seq` to `ChangePage`
   (`client.py`), thread it through `GeneratedPowerMapClient`/`pmclient` (regen the
   PM client only if the response model changed — verify first), and cover with a
   `FakeClient`/wrapper test. Red→green.
2. **Persist a replay-floor cursor.** New `SyncState` stream (e.g.
   `changes_replay`) holding the trailing seq, distinct from the live
   `changes_feed` cursor. Advance rule + margin computation (`high_water − margin`),
   env-tunable (`REPLAY_MARGIN` / cadence via `SidecarSettings`). Unit-test the
   floor/advance/margin arithmetic.
3. **Implement `replay_from_floor`** on the engine: pull the feed from the replay
   floor, apply each item via the existing `process_feed` item loop (extract the
   shared apply body so replay and live feed cannot diverge), and detect horizon
   fall-off via `min_seq` (replay floor `< min_seq` → alert + signal full-scan
   fallback). Tests: applies a skipped-commit row, is idempotent on already-current
   rows, flags fall-off.
4. **Phase A — wire replay in shadow.** Run `replay_from_floor` in the cycle (own
   session + error boundary, like the reconciles) in a **detection-only** mode that
   logs the would-heal delta (rows the replay would change that the concurrent scan
   also would) without changing the scan cadence. Surface a count on the
   `sidecar_cycle_summary` line. Assert via sidecar-level test.
5. **Phase B — widen the scan cadence + scope it to the residual.** Once Phase A
   evidence confirms replay coverage, raise `reconcile_cadence` (12h → **weekly**,
   tunable) so the full-cohort scan runs only as the residual reconcile; keep replay
   as the primary backstop. Update the cadence default + tests + the
   AGENTS.md/COMMANDS.md descriptions.
6. **Fold in conditional GET (#160) on the surviving fetch paths** — the residual
   scan's `fetch_record` and the replay's `get_entity` — sending `If-None-Match`
   and short-circuiting on `304`. **Sequenced after this (#159) lands**, tracked
   separately as #160.
7. **Docs + deploy note.** Reconcile AGENTS.md (the backstop description), the
   `RECONCILE_CADENCE`/new-knob env table, and add the Phase-A→B rollout note.

## Decisions (resolved at review, 2026-08-05)

- **Margin size** — left to implementer's judgment: a generous fixed lag sized
  above the worst-case long-txn (a harvest run). Default proposal carried into
  step 2, env-tunable.
- **Residual-scan cadence (Phase B)** — **weekly**, tunable.
- **Phasing** — **phased** (Phase A shadow → measure → Phase B cut).
- **Residual coverage completeness** — accepted, monitored risk (scan kept, not
  removed; `meta.min_seq` fall-off alerting).
- **PM client regen** — verify the `/changes` response model actually changed
  before regenerating; avoid unnecessary vendored-client churn.

## Open questions / risks

- None outstanding — all review questions resolved above. Margin default and the
  exact would-heal-delta metric shape will be pinned in-code at steps 2 and 4 and
  noted in the commit messages.
