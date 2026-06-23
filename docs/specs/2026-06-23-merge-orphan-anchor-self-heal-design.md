# Merge-orphan anchor self-heal — design

**Date:** 2026-06-23
**Status:** Approved (brainstorm) → implementation plan pending
**Related:** usa-wa#31 (backfill that surfaced this), power-map#230 (merge duplicates), power-map#231 (NULL-fill writer), power-map#235 (the `merged_into` PM follow-up filed from this design), #34 (fingerprint carry-field re-enrich)

## Problem

Power Map is the system of record for the org tree. usa-wa produces orgs, matches
them to PM, and stores PM's id as a local **anchor** (`pm_organization_id`). When a
PM curator **merges** one of our anchored orgs into a pre-existing canonical org, PM
deletes the loser and keeps the winner. Our local anchor still points at the deleted
loser, so every subsequent enrich/observe for that row fails with
`pm_id_not_found`, and the anchored-cohort reconcile 404s and silently skips it.

Observed 2026-06-23: re-running the contact-label backfill, **14 of 30** phone
committees were rejected `pm_id_not_found` — all 14 were Jun-19-created orgs that
curators had merged into the canonical WA committee tree (`01KV6PQ…` winners). The
winners carry our `org_wa_legislature_committee_id`, but nothing re-pointed our
anchors. They were healed manually this once; the class of bug is unaddressed.

Two signals for a dead anchor already exist and are both currently dropped:

- **Feed `deleted` event** — PM emits a `deleted` change for the merged loser
  (confirmed: exactly 14 `deleted` events in the feed, matching our 14 losers). The
  engine skips deletes ([`engine.py` `process_feed`](../../packages/clearinghouse-sync-powermap/src/clearinghouse_sync_powermap/engine.py): "Deletes are skipped at MVP").
- **Reconcile 404** — `_reconcile_anchored_cohort` fetches the dead anchor, gets
  `None`, and `continue`s.

The bare `deleted` event says *"loser X is gone"*, not *"merged into winner Y"*. We
recover the winner by **identifier re-match** (the winner carries our committee id).

## Goals

- Anchored rows orphaned by a PM merge re-anchor to the surviving winner
  automatically, and re-push their carry fields (display_label, acronym) to it.
- A genuinely-deleted entity (no surviving winner) is **retired locally** and never
  re-created.
- Implemented once in the shared, jurisdiction-agnostic engine
  (`clearinghouse-sync-powermap`) so it covers org/person/role/assignment and every
  sibling subscriber.

## Non-goals

- The richer PM signal (`merged_into`) — tracked as a separate power-map issue
  (see "PM follow-up"). This design works today without it.
- Changing first-time match/create behavior (still the full `pm_match` cascade).
- Backfilling/repairing existing stale anchors beyond what the new triggers heal on
  their next pass (the 14 current ones were already healed manually).

## Design

### Triggers → one routine

Both dead-anchor signals route to a single engine routine
`_heal_dead_anchor(session, descriptor, row)`:

- **Feed `deleted` event** (`process_feed`): instead of skipping, resolve the
  deleted `entity_id` to a local row via `descriptor.local_match` (by anchor). If it
  is a row we produced → `_heal_dead_anchor`. If not ours → skip as before (we do
  not mirror foreign deletes).
- **Reconcile 404** (`_reconcile_anchored_cohort`): when `fetch_record` returns
  `None`, call `_heal_dead_anchor` instead of `continue`. This is the backstop for a
  dropped/missed `deleted` feed event.

### The heal routine

```
_heal_dead_anchor(session, descriptor, row):
    winner = re-resolve by identifier (see precision note)
    if winner is not None and winner != current anchor:
        descriptor.set_anchor(row, winner)
        record = descriptor.fetch_record(client, winner)
        if record: apply_record(...)              # adopt canonical fields (LWW)
        _maybe_enqueue_enrich(check_drift=True)    # re-push carry fields to winner
    else:
        descriptor.retire(row)                     # genuine delete → retire locally
```

Re-anchor reuses the existing #34 drift machinery: after re-anchoring, the winner
lacks our `display_label`/acronym, so `_maybe_enqueue_enrich(check_drift=True)`
enqueues an ENRICH that the drain pushes up.

### Re-match precision — identifier-only

Self-heal re-resolves the winner using the **identifier stage only**, not the full
`pm_match` name/hierarchy cascade. Re-anchoring an already-produced row to the wrong
org via a fuzzy name match is worse than retiring it. So:

- identifier hit → re-anchor (high confidence — the winner provably holds our id);
- identifier miss → retire.

First-time creation keeps the full cascade (some fuzz is acceptable when minting a
brand-new link). A new descriptor method (e.g. `rematch_anchor`) exposes the
identifier-only resolution so the cascade stages aren't entangled.

**Residual risk:** if a future merge does *not* transfer the loser's identifiers to
the winner, the identifier miss would retire a row that was actually merged. This is
exactly what the PM follow-up (`merged_into`) eliminates. Until then the heal logs a
warning on the retire path so a wrongful retire is visible, and the observed PM
behavior is to transfer identifiers (all 14 winners carried ours).

### Retirement marker

- Add nullable `retired_at: timestamptz` to the four cached models
  (`canonical.organizations`, `persons`, `roles`, `assignments`) — one alembic
  migration. `grants.sql` unaffected (same tables; DML already granted).
- Engine stays model-agnostic via a `retired_column` descriptor attribute (mirrors
  the existing `anchor_column`), plus `retire(row)` / `is_retired(row)` helpers on
  the base descriptor.
- **Sweep** (`sweep_unanchored`) and **anchored-cohort reconcile**
  (`_reconcile_anchored_cohort`) both exclude `retired_at IS NOT NULL`: a retired
  row is never re-created (which would resurrect a deliberately-deleted org in PM)
  and never re-fetched.
- The shipped `FakeEntity`/`FakeDescriptor` test doubles gain `retired_at` +
  `retired_column` so engine tests exercise the generic path.

### Data flow

```
PM merge → emits deleted(loser) + updated(winner)
  usa-wa feed: deleted(loser)
    → local_match by anchor → ours
      → identifier re-match → winner
        → set_anchor(winner) + apply_record + enqueue ENRICH
          → drain pushes display_label/acronym to winner
  backstop: if the deleted event is missed,
    next anchored-cohort reconcile fetch_record(loser) → 404
      → identical _heal_dead_anchor path
```

## Components / boundaries

| Unit | Responsibility | Depends on |
|---|---|---|
| `_heal_dead_anchor` (engine) | re-anchor-or-retire one dead-anchored row | descriptor re-match, set_anchor, retire, `_maybe_enqueue_enrich` |
| `process_feed` delete branch | route an our-row `deleted` event to the heal routine | `local_match`, `_heal_dead_anchor` |
| `_reconcile_anchored_cohort` 404 branch | route a 404 to the heal routine | `_heal_dead_anchor` |
| `rematch_anchor` (descriptor) | identifier-only winner resolution | PM search (identifier filter) |
| `retire` / `is_retired` / `retired_column` (descriptor) | model-agnostic retirement marker | the model's `retired_at` |
| sweep + reconcile filters | exclude retired rows | `retired_column` |
| migration | add `retired_at` to the 4 cached models | alembic |

## Testing (TDD)

- reconcile 404 → identifier winner: re-anchors and enqueues ENRICH.
- reconcile 404 → no winner: retires (`retired_at` set), logs the warning.
- feed `deleted` for an anchored row we produced: same heal.
- feed `deleted` for an entity we never produced: no-op (skipped).
- retired row excluded from `sweep_unanchored` (not re-created).
- retired row excluded from `_reconcile_anchored_cohort` (not re-fetched).
- heal is idempotent (re-running on an already-healed row is a no-op).
- self-heal does **not** name-match: an identifier miss with a fuzzy-name candidate
  present still retires (precision over recall).

## PM follow-up (power-map#235 — SHIPPED 2026-06-23; consumed in usa-wa#37)

power-map#235 added optional `merged_into: <winner_id>` to the `deleted` change event
(option A — the `change_kind` enum is unchanged). usa-wa#37 consumes it:

- A feed `deleted` **with** `merged_into` re-anchors **any** entity type to the named
  winner generically — no per-descriptor identifier re-match, no fuzz, no
  `supports_rematch` gate (`_heal_dead_anchor(winner_hint=…)`).
- A feed `deleted` **without** `merged_into` is now a deterministic genuine delete. A
  rematch-capable descriptor (org) still runs identifier re-match first (backstop for a
  merge whose event lacked `merged_into` — a PM gap or a pre-#235 backlog delete),
  retiring only on a miss; a non-rematch type (person/role/assignment) retires directly,
  closing the prior merge/delete ambiguity.
- `rematch_anchor` / `supports_rematch` narrow to the **backstop path** — a 404 reconcile
  *or* a bare `deleted` feed event with no `merged_into` — org-only, by identifier.

## Open questions / risks (resolved by power-map#235 / usa-wa#37)

- **Identifier transfer assumption** — RESOLVED. The feed re-anchor is now driven by
  PM's explicit `merged_into`, not the identifier-transfer heuristic. The heuristic
  survives only as the org-only 404 backstop.
- **Person/role/assignment** — RESOLVED on the feed path: they re-anchor generically
  from `merged_into` and retire on a genuine delete. Inert only if a dead anchor is
  *first* seen via the 404 backstop (no `merged_into` there) — degraded, not wrong.
- **Migration ordering** — `retired_at` is additive/nullable; no backfill needed.
  Deploy is the standard migrate-then-restart (units are `--no-sync`). usa-wa#37 adds
  no migration (the `retired_at` columns already exist on all four cached models).
- **Read-path leakage** — RESOLVED (usa-wa#38). Retirement keeps the row as provenance
  (no hard-delete) but leaves a dead anchor, so it must not surface in *live* reads.
  Audit found no user-facing read surface yet; the guardrail is a shared
  `queries.exclude_retired(stmt, *models, include_retired=…)` helper (backed by
  `RetirableMixin.not_retired()`) that every read routes through, plus a fix to
  `backfill_contact_labels` to skip retired orgs. No retirement cascade — each entity
  heals on its own anchor.
