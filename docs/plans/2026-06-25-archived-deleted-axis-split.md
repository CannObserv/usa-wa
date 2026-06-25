---
title: Split archived vs deleted lifecycle axes (retired_at → archived_at + deleted_at)
date: 2026-06-25
status: implemented
---

# Split archived vs deleted lifecycle axes

Issue: [usa-wa#42](https://github.com/CannObserv/usa-wa/issues/42)
Sibling (deferred): [usa-wa#43](https://github.com/CannObserv/usa-wa/issues/43) / [power-map#240](https://github.com/CannObserv/power-map/issues/240) — the third (`active`) axis.

## Problem

`retired_at` is overloaded across two of PM's three orthogonal lifecycle axes,
which have **opposite re-fetch semantics**:

| Retire reason | PM anchor id | Reconcile must |
|---|---|---|
| genuine delete / merge-orphan-no-winner (#31/#36/#37/#38) | dead (404) | **skip** (re-fetch 404s) |
| PM archival (#40/#41), reversible | **live** | **re-fetch** (to catch un-archive) |

The reconcile/sweep filters (`engine.py:316`, `engine.py:1123`) and the live-read
guard (`exclude_retired`) all test the single `retired_at IS NULL` column, so they
cannot tell the two apart. Consequence (#42): a **dropped un-archive feed event**
is never recovered by the anchored-cohort reconcile backstop — the archival row is
filtered out as if it were a dead-anchor tombstone, and stays invisible in live
reads forever despite being active in PM. Silent, self-heal-proof, exactly the
dropped-event class the backstop exists to cover.

PM models these as distinct axes (`archived_at` reversible gate vs the
`deleted_entities` terminal tombstone). usa-wa collapsed them; the fix is to
un-collapse, mirroring PM's nomenclature 1:1.

## Approach

Replace the one overloaded column with **two PM-parity columns** on the identity
models:

- **`archived_at`** — mirrors PM's `archived_at` exactly (reversible; live anchor).
  Set/cleared by `mirror_archival` from PM's own clock. Excluded from live reads,
  **kept in** the reconcile/sweep cohort so un-archive is recovered.
- **`deleted_at`** — terminal tombstone (genuine delete / merge-orphan-no-winner;
  dead anchor). Stamped only by `_heal_dead_anchor`. Excluded from live reads **and**
  from the reconcile/sweep cohort (re-fetch would 404).

The #42 fix then falls out: reconcile/sweep filter on `deleted_at IS NULL` only, so
archival rows stay re-fetchable; `get_entity` returns archived entities with
`archived_at` populated (confirmed with PM owner 2026-06-25 — the detail endpoint
always returns the row regardless of archive state; `include_archived=true` is a
*list*-endpoint flag only), so the next reconcile observes the un-archive and clears
`archived_at`.

Nomenclature churn accepted (user, 2026-06-25): `retired_*` → split into
`archived_*` / `deleted_*` everywhere, dropping the overloaded "retired" term.

### Naming map

| Today | After |
|---|---|
| `RetirableMixin` | `LifecycleMixin` (carries both `archived_at` + `deleted_at`) |
| `RetirableMixin.not_retired()` | `not_deleted()` (`deleted_at IS NULL`) **and** new `is_live()` (`deleted_at IS NULL AND archived_at IS NULL`) |
| `EntityDescriptor.retired_column` | `deleted_column` (terminal) + new `archived_column` |
| `retired_column_expr()` / `is_retired()` / `retire()` | `deleted_column_expr()` / `is_deleted()` / `mark_deleted()` |
| `mirror_archival` → sets `retired_column` | → sets/clears `archived_column` |
| `exclude_retired(stmt, *models, include_retired=)` | `live_only(stmt, *models, include_hidden=)` — filters `is_live()` (both columns) |

Reconcile/sweep use `not_deleted()` (terminal only); live reads use `live_only` /
`is_live()` (both).

### Data migration (the safe direction)

The existing `retired_at` values cannot be disambiguated historically — each could
be archival or genuine-delete. Migrate by **renaming `retired_at` → `archived_at`**
(all existing tombstones become *archival*) and adding an empty `deleted_at`.

This is the **self-correcting** direction: a row that was *really* a genuine delete
now sits in the reconcile cohort, gets re-fetched, 404s, and `_heal_dead_anchor`
stamps `deleted_at` (clearing `archived_at`) on the next cycle. The reverse
(everything → `deleted_at`) would *not* self-correct — deleted rows are filtered
from reconcile and never re-fetched, freezing the #42 bug for any archival row that
existed pre-migration. So: rename into `archived_at`, let reconcile sort out the
genuine deletes.

`_heal_dead_anchor` must therefore **clear `archived_at` when it stamps
`deleted_at`** (terminal supersedes reversible).

## Tradeoffs / alternatives

- **`retire_reason` enum on one column** (issue direction 1, enum form) — rejected:
  the two states have different lifecycles (reversible vs terminal) and different
  clocks (PM's `archived_at` vs our heal stamp); two nullable timestamps model that
  honestly and read 1:1 against PM's schema. An enum forces every reader to branch.
- **Keep `retired_at` name, add `archived_at` only** — rejected by user; full
  parity churn to `deleted_at` accepted so the local vocabulary matches PM.
- **Accept the gap, document it** (issue direction 3) — rejected: it's a silent,
  self-heal-proof data-correctness gap, cheap to close now that the axes are named.
- **Migrate existing tombstones → `deleted_at`** — rejected: not self-correcting
  (see Data migration), would freeze #42 for pre-existing archival rows.

## Steps (TDD: red → green per step)

1. **Regression test first (red).** Engine reconcile test: a row with
   `archived_at` set, `deleted_at` NULL, live anchor; PM now returns
   `archived_at=null`; assert reconcile re-fetches, clears `archived_at`, row
   revives. Inverse: a `deleted_at`-set row is **not** re-fetched. (Sibling test
   doubles in `clearinghouse_sync_powermap/testing.py` need the two-column shape.)
2. **`LifecycleMixin`** (identity.py): rename `retired_at`→`deleted_at`, add
   `archived_at`; `not_deleted()` + `is_live()`; update dual-meaning docstring to
   one-meaning-each.
3. **`live_only`** (queries.py): rename `exclude_retired`, filter `is_live()` (both
   columns), `include_hidden` escape hatch; update module docstring + the
   `backfill_contact_labels.py` call site.
4. **`EntityDescriptor`** (descriptors.py): `deleted_column` + `archived_column`;
   rename `retired_column_expr`/`is_retired`/`retire`; `mirror_archival` →
   `archived_column` set-or-clear; drop the #42 NOTE from its docstring (fixed).
5. **Engine** (engine.py:316, :1123, `_heal_dead_anchor`): filters → `not_deleted()`;
   heal stamps `deleted_at` and clears `archived_at`.
6. **Concrete descriptors** (org/person/role/assignment): `deleted_column="deleted_at"`
   + `archived_column="archived_at"`; refresh inline comments.
7. **Alembic migration**: `RENAME COLUMN retired_at TO archived_at` + `ADD COLUMN
   deleted_at` on all four identity tables; downgrade reverses. (No `grants.sql`
   change — same schema.)
8. **Green**: full suite + `ruff`. Migration applied via `usa-wa-migrate` on deploy.

## Open questions

1. `LifecycleMixin` carries `deleted_at` for all four entities, but PM never emits
   `deleted` for roles/assignments (your caveat 4) — their `deleted_at` can only be
   set by a `_heal_dead_anchor` 404 (parent gone). Keep the column on all four for
   uniformity (benign-always-NULL on roles/assignments), or scope `deleted_at` to
   person/org only? Leaning **keep uniform** — simpler mixin, matches today's shape.
2. Read helper name: `live_only` vs `visible_only` vs `exclude_hidden`. Avoided
   `exclude_inactive` to not collide with PM's separate `active` axis (#43). OK?
