---
title: "#144 Phase 2 — assignment retraction (retire Wynne's spurious PM-anchored rows)"
date: 2026-08-05
---

# #144 Phase 2 — producer assignment retraction

## Problem

Phase 1 stopped the Wynne LD39-Senate-2001-02 artifact from being *re-derived*, but the two
already-produced, PM-anchored assignments (`481:chamber-senate:39:2001-02` + `481:party:republican:
2001-02`) still exist locally (closed, `is_active=f`) and in Power Map. They surface in the
`succession_invariants --sweep-biennia` historical audit. power-map#391 now ships the producer
retraction verb (`op:"retract"` on `AssignmentObservationRequest`, v0.20.0), so we can retire them
cleanly through the sanctioned `/observations` channel — no orphan, no `/admin/` route.

## Approach

A targeted one-shot CLI `retract_assignments.py` (usa-wa-sync-powermap), mirroring the
`heal_assignment_clocks` one-shot. Given local assignment `source_id`s, it resolves each live
anchored row, POSTs `{"identifier_type":"pm_assignment_id","identifier_value":<ulid>,"op":"retract"}`
to `/api/v1/assignments/observations` via the existing `post_observation` wrapper, and on a
`retracted` disposition tombstones the local row (`archived_at`, the reversible lifecycle axis —
`_seat_scope` already excludes archived rows in every mode, so the sweep clears immediately). PM's
**anti-resurrection** at both create doors + Phase 1's derivation exclusion are belt-and-suspenders
against the phantom span ever returning.

**No PM client regen needed** — `op` round-trips through the vendored model's `additional_properties`
(proven), exactly the #111 pattern the `unapplied` field already uses; `disposition` is a plain
`str`, so `retracted` flows through. (A full regen is a large, orthogonal diff; deferred.)

**Retraction is terminal** (power-map#391 deliberately did not ship reversible `archived:false`;
un-retract is an admin-only PM operation). The CLI must not build retry against un-retract, and the
local tombstone is `archived_at` (reversible axis) so an admin unarchive on PM flows back via the
read-mirror.

## Tradeoffs / alternatives

- **General descriptor path** (tombstone an anchored assignment → drain auto-emits `op=retract`) —
  rejected: retraction is rare and deliberate; a general path complicates the hot sync loop for a
  handful of curated artifacts. The committee-event producer's retract is event-scoped; assignments
  get a focused one-shot instead, consistent with the migrate/heal CLIs.
- **Full PM client regen** — deferred: unnecessary (additional_properties round-trip works) and a
  large, noisy diff of unrelated v0.20.0 changes that could break `pmclient.py` dispatch.
- **Hard-delete the local rows** — rejected: loses provenance/history and needs the owner role; the
  archived tombstone is the modeled retraction mirror (#41/#42) and app-role DML.

## Steps

1. **RED** — `test_retract_assignments.py` with a fake `post_observation` client: retracting an
   anchored row POSTs the id-addressed `op:"retract"` payload and sets `archived_at`; an unanchored
   or not-found `source_id` is skipped-and-counted, not retracted; an unexpected disposition does
   **not** tombstone.
2. **GREEN** — `retract_assignments.py`: `retract_assignments(session, client, source_ids)` +
   `main` (`--source-id` repeatable, `--dry-run`; exit 2 on `DeliveryBlockedError`).
3. **Verify** — full suite green; ruff clean.
4. AGENTS.md: add the CLI to the module map + the COMMANDS table; note it's the #144 Phase 2 tool.
5. Ship (merge main, push, comment #144).
6. **Execute for Wynne** (sidecar paused): dry-run then real-run the two `source_id`s; confirm PM
   `retracted` + local `archived_at` set; `succession_invariants --sweep-biennia --strict` no longer
   flags LD39 Senate 2001-02; daily gate still 49/98; resume sidecar; confirm no orphan / no churn.

## Open questions / risks

- **Idempotent re-run**: a second retract of an already-archived tenure — PM's already-archived
  no-op path (#391) should return quietly; the CLI treats any non-`retracted` disposition as
  unexpected-and-logged (not a tombstone), and the row is already archived locally, so a re-run is
  safe. Confirm the disposition PM returns on re-retract during execution.
- Low risk: two rows, terminal but modeled (archived, not deleted), sidecar paused, `--dry-run`
  first, and the daily open-cohort gate is unaffected (the rows are already closed).
