---
title: Engine-level merge-orphan anchor self-heal
date: 2026-06-23
status: draft
---

# Engine-level merge-orphan anchor self-heal

Spec: [`docs/specs/2026-06-23-merge-orphan-anchor-self-heal-design.md`](../specs/2026-06-23-merge-orphan-anchor-self-heal-design.md)

## Problem

A PM-side org merge deletes the loser and keeps the winner, but usa-wa's local
anchor still points at the deleted loser — every later observation fails
`pm_id_not_found` and the reconcile 404s and skips. PM already emits the recovery
signals (a `deleted` feed event for the loser; a 404 on re-fetch) but the engine
drops both. Surfaced by usa-wa#31: 14/30 phone committees were orphaned by merges
and had to be re-anchored by hand.

## Approach

Route both dead-anchor signals — the `deleted` feed event and the reconcile 404 — to
one engine routine `_heal_dead_anchor`. It re-resolves the winner by **identifier
only** (precision over recall): a hit re-anchors the row, adopts the winner's
canonical fields, and re-enqueues an ENRICH so the carry fields (display_label,
acronym) re-push to the winner via the existing #34 drift machinery. A descriptor
that supports re-match but finds no winner **retires** the row (`retired_at`); a
descriptor that doesn't support re-match logs an unhealed-anchor warning and skips
(safer than wrongly retiring a possibly-merged person/role). Retired rows are
excluded from the sweep and the anchored-cohort reconcile so a deliberately-deleted
entity is never re-created or re-fetched. Org gets a concrete identifier re-match;
person/role/assignment fall back to log-and-skip until power-map#235 (`merged_into`)
lets the engine re-anchor every entity type generically.

## Tradeoffs / alternatives

- **Reconcile-404 trigger only (skip the feed `deleted` path)** — rejected: we
  already receive the `deleted` event, and 404-only healing is cadence-delayed; wiring
  both is cheap defense-in-depth.
- **Full `pm_match` cascade (identifier→name→hierarchy) for re-match** — rejected:
  fuzzy name-matching an already-produced row risks re-anchoring to the *wrong* org;
  retire-on-identifier-miss is safer (spec §3, user-confirmed).
- **Retire unsupported-descriptor dead anchors too** — rejected: retiring a
  possibly-merged person/role is destructive without an identifier signal; log-and-skip
  is conservative until power-map#235 lands.
- **Wait for power-map#235 and consume `merged_into` only** — rejected: usa-wa owns
  its anchors and can't depend on signal completeness; it must self-heal today.

## Steps

1. **Base descriptor plumbing** (`clearinghouse_sync_powermap/descriptors.py`): add
   `retired_column: str | None = None`, `is_retired(row)`, `retire(row)` (stamp the
   declared column with `now`, UTC), `supports_rematch: bool = False`, and a
   `rematch_anchor(client, session, row)` contract (default unused). Unit-test via a
   `FakeDescriptor`/`FakeEntity` extended with `retired_at` + `retired_column`.
2. **Retirement column + migration**: add nullable `retired_at` (timestamptz) to
   `Organization`, `Person`, `Role`, `Assignment` in clearinghouse-domain-legislative;
   one alembic autogen migration; verify `grants.sql` needs no change (same tables).
   Point each descriptor's `retired_column` at `"retired_at"`.
3. **Org identifier re-match** (`descriptors/organization.py`): implement
   `rematch_anchor` as identifier-only resolution (`identifier_type_for` +
   `search_entities(identifier_type=…, identifier_value=…)`), `supports_rematch = True`.
   Tests: hit → winner id; miss → None.
4. **`_heal_dead_anchor` engine routine** (`engine.py`): winner → `set_anchor` +
   `fetch_record` + `apply_record` + `_maybe_enqueue_enrich(check_drift=True)`;
   supported-but-no-winner → `descriptor.retire(row)` + warning; unsupported → warning
   + skip. Unit tests for all three branches + idempotency.
5. **Wire reconcile 404** (`_reconcile_anchored_cohort`): replace the `record is None`
   `continue` with `_heal_dead_anchor`. Test: 404→winner re-anchors+enqueues enrich;
   404→no-winner retires.
6. **Wire feed `deleted`** (`process_feed`): on `change_kind == "deleted"`,
   `local_match` the id by anchor; ours → `_heal_dead_anchor`; not ours → skip as
   today. Tests: deleted-for-ours heals; deleted-for-foreign is a no-op.
7. **Exclude retired rows** from `sweep_unanchored` and `_reconcile_anchored_cohort`
   (add `retired_column IS NULL` to both selects, via a `retired` expr helper). Tests:
   retired row not re-created by sweep; not re-fetched by reconcile.
8. **Verify + deploy**: full `pytest` + ruff green; `sudo systemctl start
   usa-wa-migrate` (after `uv sync --locked` if `uv.lock` moved) then restart
   `usa-wa-sync-powermap`. Confirm startup fingerprint.

## Open questions / risks

- **Person/role/assignment re-match** — deferred to log-and-skip (no identifier
  re-match implemented); a merged person retires/skips rather than re-anchors until
  power-map#235 supplies `merged_into`, which will let `_heal_dead_anchor` re-anchor
  any entity type generically (a small follow-up once the regen brings the field).
  User-accepted.
- **Identifier-transfer assumption** for the org re-match — resolved deterministically
  by power-map#235; the retire path logs a warning so a wrongful retire is visible.
- **Migration** — `retired_at` is additive/nullable, no backfill; standard
  migrate-then-restart (units are `--no-sync`, so `uv sync --locked` first only if
  `uv.lock` changed).
