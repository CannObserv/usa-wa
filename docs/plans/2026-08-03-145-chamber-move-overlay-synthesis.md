---
title: Chamber-move succession remediation — mover-gated closed-span overlay synthesis (#145)
date: 2026-08-03
status: draft
---

# Chamber-move succession remediation — mover-gated closed-span overlay synthesis

## Problem

The #145 Bucket-A backfill re-dates genuine mid-biennium successions via operator events. It works for pure Senate vacancies but **corrupts the record for House→Senate chamber-moves** (~20 of the 54 cases). Confirmed by the reverted 2013-14 tranche: `seated`-ing a mover marks their Id in `event_member_ids`, which [house/build.py](../../packages/usa-wa-adapter-sos/src/usa_wa_adapter_sos/house/build.py) feeds as `keep_ids` into `build_house_roster` — re-including the mover, which was correctly dropped by the #105 mover-exclusion. The re-included mover re-runs the #103 within-LD elimination, **splitting the real backfiller's tenure** into superseded `:YYYY-YY` duplicate spans that collide across every later biennium (sweep went 57→71). The re-inclusion exists only because the overlay's `vacated` needs a *built* span to close. The split also propagated 5 zombie assignments to Power Map before it was caught.

## Approach

Decouple "date the mover's House span" from "re-include the mover in the elimination." Add a **mover-gated closed-span synthesis** to the operator overlay: when a `vacated chamber-house` event matches no built span *and* the member is a known mover for that biennium, synthesize a **closed** span `[biennium-floor(effective_date) → effective_date]` keyed on that biennium — instead of relying on roster re-inclusion. Then **stop passing movers in `keep_ids`** so the elimination sees the normal, unperturbed roster and the backfiller is never split. A chamber-move case is then four events — `departed`(outgoing senator), `vacated chamber-house`(mover → synthesized), `seated chamber-senate`(mover, built normally), `seated chamber-house`(backfiller, re-dates its real start) — and produces **zero superseded rows**, so no `house.migrate` and no PM anchor churn. Closed-span synthesis is safe where the #119 open-synth guard wasn't: a closed historical span can't inflate the current open-chamber count. The mover-signal gate (member's WSL Id in a Senate row that biennium — already computed by the mover-exclusion) prevents a typo'd `vacated` from minting a bogus closed span.

## Tradeoffs / alternatives

- **Keep re-inclusion + run `house.migrate` in the flow** — rejected: every chamber-move rebuild re-splits the backfiller and needs a migrate to collapse it, which orphans PM anchors on each run (the exact failure just remediated). Trades a one-time mechanism cost for permanent churn.
- **Synthesize any unmatched `vacated` (no mover gate)** — rejected: simpler, but a typo'd `vacated` mints a closed span that can false-conflict; the mover signal is already available at the call site, so the gate is nearly free.
- **Fix the elimination to tolerate the re-included mover** — rejected: the perturbation is diffuse (back-chain + elimination re-key collateral spans beyond the mover's own LD); hardening every path is more fragile than keeping the mover out of the roster entirely.
- **Leave chamber-moves at biennium-granular overlap (do nothing)** — rejected: the user chose full remediation; these are ~20 real successions and #145's default only covers cases that can't be dated, which these can.

## Steps

**Status (2026-08-03): steps 1–4 done + committed (694 tests pass); 5–6 pending deploy.**

1. ✅ **Overlay synthesis (pure, test-first).** Add a `movers_by_biennium: dict[str, set[str]]` param to `apply_operator_events`. In the `vacated` no-match branch, if `event.member_id ∈ movers_by_biennium[biennium(effective_date)]`, append `_synthesize_closed(event)` = `[floor(effective_date) → effective_date]` keyed on that biennium; else keep the existing `operator_vacated_no_span` log. Unit-test the pure function: mover → closed span minted; non-mover → no-op; existing seated open-synth unchanged.
2. ✅ **Compute + thread the mover signal.** Have the roster layer expose the per-biennium mover-Id set (the Ids `build_house_roster` drops via the #105 exclusion) so `house/build.py` can pass `movers_by_biennium` to the overlay. Senate/committee builders pass `{}` (no House movers to synthesize).
3. ✅ **Drop movers from `keep_ids`.** In `house/build.py`, exclude mover Ids from the `keep_ids=event_members` set so an operator-touched mover stays mover-excluded (their House span now comes from synthesis, not re-inclusion). Keep non-mover event members (stale exemption) unchanged.
4. ✅ **Regression + invariant guards.** (a) A golden chamber-move fixture (mover House synth + backfiller reseat + outgoing departed) asserting the built span set has **zero superseded duplicates** (nothing for `house.migrate` to collapse) and the `--as-of` sweep is clean. (b) A Hunt-2025 (current-biennium mover) regression test — her House span still closes at her chamber-move date via synthesis, not re-inclusion.
5. ⬜ **Backfiller research.** Second research pass for the ~20 chamber-move backfillers' appointment dates + evidence (Ortiz-Self/Peterson/Robinson/Walkinshaw/Pike/Wylie/Pollet/Muri/…), extending the #145 table.
6. ⬜ **Remediate tranche-by-tranche.** Per biennium: record the 4-event-per-move batch (dry-run → record), one unrestricted `harvest_sponsor_spans` + `house.build`, then verify `succession_invariants --as-of <YYYY-01-01> --strict` == 0 **and** the global sweep strictly decreases with **no** new conflicts and **no** superseded rows. Start with 2013-14 (the reverted tranche) as the re-proof.

## Open questions / risks (resolved 2026-08-03)

- **`house.build` unrestricted vs. daily-restricted — CONFIRM via test.** The mover-signal + synthesis must behave identically in both; step 4 adds an explicit restricted-rebuild test asserting the daily (current-cohort) run does **not** synthesize a historical mover (mover-gate + `biennium(effective_date)` keying confine it, mirroring the #119 open-synth guard).
- **Stale (non-mover) event members remain in `keep_ids` — NOTE only.** Out of scope; a code comment records that a future stale case perturbing the elimination would need the same synthesis treatment.
- **PM propagation — PAUSE the sidecar per-tranche.** `sudo systemctl stop usa-wa-sync-powermap` before each remediation tranche; verify zero superseded rows + clean `--as-of --strict` locally; only then resume so the sidecar drains the corrected state. Do this until the no-churn property is proven once live, then reassess.
- **Imprecise backfiller dates — PROMPT per-tranche.** Where a backfiller start can't be firmly dated, surface it and ask the user for the date/decision **before** executing that tranche rather than guessing; a move that stays undatable is left biennium-granular.
