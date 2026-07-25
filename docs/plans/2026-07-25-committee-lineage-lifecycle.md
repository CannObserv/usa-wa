---
title: Committee lineage & lifecycle — consume power-map#321 events for a coherent Org timeline
date: 2026-07-25
status: draft
---

# Committee lineage & lifecycle (usa-wa#124)

Design spec: [`docs/specs/2026-07-25-committee-lineage-lifecycle-design.md`](../specs/2026-07-25-committee-lineage-lifecycle-design.md). This plan is the *how* + build order; the spec holds the model and the resolved decisions.

## Problem

The `harvest_committees` model-A backfill left ~150 historical committee Orgs stuck `active=true`, lifespan-unbounded, because `reconcile_committee_active`'s #90 live-era scoping excludes non-current WSL `Id`s from retirement. There is no coherent Org timeline and no cross-`Id` succession record (the "Senate Labor & Commerce" lineage: 3+ era `Id`s all `active=true`, only 28244 holding live members). power-map#321 shipped the event surface to fix this; usa-wa must now consume it.

## Approach

Build the three-axis model from the spec, in dependency order, as **separate PRs under #124**. Axis 1 (`active`, per-`Id`, no grouping) rides the existing `reconcile_committee_active` producer once the #90 scoping is relaxed. Axes 2–3 (lifecycle windows + succession links) become PM **entity events**: `founded`/`dissolved` auto-derived from each `Id`'s roster biennium range, `succeeded_by`/`split_from`/`merged_with` operator-attested via a new store+CLI modeled on #107. A new event-producer descriptor emits both through the **partial-success sub-resource** (`POST /orgs/{id}/events/observations`), carrying local anchored event rows addressed by `pm_event_id` for refine-in-place (the #311 assignment dual-mode) with a diff-before-write no-op gate. Coverage validated by a read-only invariant (à la `succession_invariants.py`); an advisory curation-assist report helps the operator find succession pairs. The PM client is already regenerated (this branch); everything downstream builds on the wrapper seam added in step 1.

## Tradeoffs / alternatives

- **Embedded event writes instead of the sub-resource** — rejected: the embedded `ObservationResponse.events` path is all-or-nothing, so one bad `succeeded_by` (unanchored successor) would roll back an org's whole observation; the sub-resource gives per-event savepoints + ordering-tolerance (spec § B3a).
- **`(source, source_id)` producer natural key (anchor A) instead of `pm_event_id`** — rejected (spec § B2): our producers already carry local anchors, so A's stateless benefit is unused and it adds a 4th PM idempotency primitive.
- **Fully-automatic succession inference** — rejected: WA re-orgs are irregular (splits/merges), no ground truth; grouping/linking is operator-attested, with automation only as the advisory C5 report.
- **One mega-PR** — rejected: the five sub-systems have clean seams; phased PRs keep each reviewable and let the low-risk auto layer (windows, deactivation) land before the operator surface.

## Steps

1. **PM client wrapper seam.** Extend the `PowerMapClient` Protocol + `GeneratedPowerMapClient` (`pmclient.py`) with `submit_org_event_observations(org_id, events) -> [EventObservationResult]`, mapping our event value-types onto `OrgEventObservationsRequest`/`ObservationEventItem` and parsing `disposition`/`event_id`/`reason`. Add a shipped test double in `clearinghouse_sync_powermap/testing.py`. TDD; wrapper round-trip test. *(Client regen already committed on this branch.)*
2. **C1a — window derivation (pure).** New module deriving each committee `Id`'s `founded` (first observed biennium start) / `dissolved` (last observed biennium end, non-heads only) from `CommitteeRosterCohortProvider`, with `founded` floor-gated (emit only when first-observed is ≥ N bienniums after the 1999-00 archive floor — pick N in step's test). Pure functions, TDD.
3. **C1b — deactivation.** Adjust `reconcile_committee_active` so a committee `Id` absent from the current-biennium live roster resolves to `active=false` (relax the #90 `scoped_out` exclusion to a deactivation with the window as evidence), preserving the mass-close floor for steady-state partial-pull safety. Verify the one-time ~150 flip is gated behind an explicit `--max-close-fraction 1.0` run.
4. **C2 — operator attestation store + CLI.** Append-only, `usa_wa_operator`-sourced store for `succeeded_by`/`split_from`/`merged_with` attestations (natural-keyed on `(predecessor Id, successor Id, slug, date)`, supersede-for-corrections), plus a CLI interjection surface (direct-arg / `--file` / `--supersede` / `--list` / `--dry-run`) validating both ends resolve to committee Orgs. Modeled on `operator_events.py`. Migration for the new table (owner role); TDD.
5. **C3 — event producer descriptor.** Emit C1a windows + C2 links through the step-1 wrapper: local anchored event rows (`entity_events`, `usa_wa`-sourced, distinct from the mirror rows), unanchored→create / `pm_event_id`→refine-in-place, diff-before-write no-op gate (`docs/LWW-NOOP-GATE.md`). Route `rejected` reason slugs (`linked_entity_unresolved` transient vs terminal) into the #85 rejection-visibility summary + #112 non-convergence tracking. Wire into the daily refresh (best-effort SAVEPOINT, current-biennium-scoped). TDD against the step-1 double.
6. **C4 — coherence invariant + timer.** Read-only check: no inactive/dissolved committee carries an `is_active` assignment; the current head is the sole active Org in its lineage (keyed on attested links). Exit 1 → operator email; new systemd oneshot+timer with `OnFailure=` + the `assert-main-checkout` guard; add to `test_unit_ordering.py`.
7. **C5 — curation-assist report (read-only).** Lineage-candidate suggestions by name-similarity + membership-carryover across the biennium boundary + adjacent windows + chamber. Advisory only (asserts nothing); a CLI that prints candidate groups for the operator to attest in C2.
8. **Backfill run + rollout.** Sidecar-paused window: run C1a/C1b (windows + one-time deactivation), let the operator attest links via C2/C5, produce events via C3, then enable C4. Sequence per spec § "Rollout order"; document the ad-hoc commands in `docs/COMMANDS.md`.

## Open questions / risks

- **`active ⟺ current-roster presence` may over-deactivate** a committee that exists but didn't roster this biennium (dormant, not dissolved). Mitigations: daily discovery re-creates genuinely-current committees; the mass-close floor guards bulk flips. Confirm no standing committee is legitimately absent from a current-biennium roster before the one-time deactivation, and consider a "present in current *or prior* biennium" softening for the head test.
- **power-map#322 (event void/retract) not yet shipped.** C2 supersede lands locally, but a producer *re-link* correction (wrong successor) cannot retract the stale PM event until #322 — the erroneous linked event persists (admin-retractable interim, #313 precedent). C2/C3 must not block on it; date-refine (the common path) is fully covered by #321.
- **Local `entity_events` producer rows vs the read-mirror.** `sync_entity_events` prunes locally-anchored rows PM stops reporting; a `usa_wa`-sourced producer row is safe once anchored (PM reports it back) but unanchored pre-emit rows must not be swept. Needs an explicit interaction test in step 5.
- **`founded` floor-gate threshold N** (step 2) is a judgment call — too tight omits real foundings, too loose asserts wrong years. Decide empirically against the archived roster range.
- **Ordering during backfill:** succession links reference successor Orgs that must be anchored first; partial-success makes this self-healing (transient `linked_entity_unresolved`), but the backfill should still emit windows/deactivation before links to minimize churn.
