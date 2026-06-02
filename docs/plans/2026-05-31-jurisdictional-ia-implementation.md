---
title: Implement Jurisdictional IA — local cache + schema-wide jurisdiction_id FK refactor + PM feature request
date: 2026-05-31
status: refreshed 2026-06-01 against PM #168 Phase 1 + Phase 2 shipped
---

# Jurisdictional IA — implementation plan

## Problem

`Role.district: text(32)` is an unanchored label; `jurisdiction_id: text(32)` across ~30 canonical tables is a partition tag with no referential integrity, no hierarchy, no temporal validity. The brainstorm-approved design at [`docs/specs/2026-05-31-jurisdictional-ia-design.md`](../specs/2026-05-31-jurisdictional-ia-design.md) extends Power Map with a Jurisdiction entity type (graph + bitemporal; spatial deferred), and refactors usa-wa to consume via a local cache that mirrors PM's shape. This plan lands the usa-wa-side work (which is mostly independent of PM endpoints) and files the upstream feature request so PM can build in parallel.

## Approach

Land the local schema work in one migration: create **four cache tables** (`clearinghouse_core.jurisdictions`, `jurisdiction_types` lookup, `jurisdiction_relationships`, `jurisdiction_relationship_types` lookup), seed the type vocab (16 rows) + relationship-type vocab (**11 codes** — PM dropped `exercises_concurrent_jurisdiction`) + the WA-relevant jurisdiction set (39 counties + 49 LDs + 10 CDs + Seattle + state + country), and refactor every `jurisdiction_id: text(32)` column across ~30 canonical tables to `ULID NOT NULL FK`. Drop `Role.district`. Update tests for every fixture that seeded jurisdiction text. **`clearinghouse_core.jurisdiction_identifiers` is dropped from this plan** — PM's design review chose to extend the existing `identifiers` polymorphic table rather than create a separate one; for usa-wa MVP, local identifier caching is deferred to the sidecar follow-up plan.

**Sidecar readiness:** PM #168 Phase 1 (read) and Phase 2 (write) both shipped 2026-06-01, so sidecar work is no longer blocked. Implementation remains a separate follow-up plan rather than expanding scope here — this plan stays focused on local schema work.

## Tradeoffs / alternatives

- **Local-only hierarchy table (no PM integration at all)** — rejected because identity (Person / Org / Role / Assignment) already flows through PM via the producer/archival pattern; jurisdictions diverging from PM creates two parallel identity surfaces. Cohort coherence is the win.
- **Defer the full schema-wide FK refactor; ship only Role.jurisdiction_id refactor + the cache tables** — rejected because the partial refactor leaves jurisdiction_id text(32) in place across the rest of the schema, requiring a second migration later. Cleaner to do once while there's no production data to backfill.
- **Wait for PM to ship the Jurisdiction extension before doing any usa-wa work** — rejected because most of the usa-wa work (cache models + schema refactor + pre-seed) is independent of PM endpoints. The sidecar is the only PM-blocked piece, and it's already deferred to P1+ in the design.
- **Build a sibling Jurisdiction-as-a-Service (the JaaS path from the external exploration)** — rejected during the brainstorm; user picked "extend Power Map" over "new sibling service." Re-evaluate if PM declines the feature request.

## Common gates (every code-touching step)

Every step that modifies code or schema must close clean against:

- `uv run pytest` — all tests pass at the workspace coverage threshold (80%).
- `uv run ruff check .` — lint clean.
- `uv run ruff format --check .` — format clean.

Step-specific verifiable-when notes below add the per-step criteria on top of these gates.

## Steps

1. **File the upstream CannObserv/power-map feature request EARLY.** ✅ Filed 2026-05-31 as [CannObserv/power-map#168](https://github.com/CannObserv/power-map/issues/168). ✅ **PM Phase 1 (5 read routes, 38 tests) shipped 2026-06-01** (commits f51178a → f1f9bf1; merged 6b0a8bf). ✅ **PM Phase 2 (`POST /api/v1/jurisdictions/observations`, 17 tests) shipped 2026-06-01** (commits 5287360 → 42d5b94). PM design review surfaced eight refinements all incorporated into the spec body (see Changelog at top of design spec). **Net effect for this plan:** the PM contract is now real; steps 3–4 below reflect PM's shipped shape (FK lookup for `type`, 11-code vocab with `is_symmetric`, `valid_from`/`valid_until`/`recorded_at`/`superseded_at` bitemporal naming, no separate `jurisdiction_identifiers` table).

2. **Produce `initial_jurisdictions.json` proposal + obtain user review approval.** ✅ Completed 2026-06-01. File at `packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/data/initial_jurisdictions.json`. Final tally: 101 jurisdictions (1 country + 1 state + 49 LDs (generic `legislative_district` type) + 10 CDs + 39 counties + Seattle) and 101 relationships (the 100 state-level + country-level containments plus Seattle additionally contained by King County as a worked example of the city-in-county graph relation). Naming conventions verified against Section 1 slug rules; multi-word county names use `_` substitution (`grays_harbor`, `pend_oreille`, `san_juan`, `walla_walla`).

3. **Add the four cache models in `clearinghouse_core`.** `clearinghouse_core.jurisdictions` (replaces the existing minimal one — `JurisdictionLevel` StrEnum drops; `type_id` ULID FK to the new lookup; `valid_from` / `valid_until` / `recorded_at` / `superseded_at` bitemporal columns), `jurisdiction_types` (16-row lookup seeded to match PM), `jurisdiction_relationship_types` (11-row lookup with `is_symmetric` + `category`; `exercises_concurrent_jurisdiction` excluded per PM Phase 1), `jurisdiction_relationships` (bitemporal junction). **`jurisdiction_identifiers` is NOT added** — PM extended the existing `identifiers` table on their side; usa-wa defers local identifier caching to the sidecar follow-up. Side-effect imports added to `clearinghouse_core/models.py` (the existing `clearinghouse_core/__init__.py` already triggers the registration chain via `from clearinghouse_core import models`). **Verifiable when:** common gates pass; `uv run alembic check` reports drift = exactly the new tables (proves `Base.metadata` sees them; drift is only fully resolved by step 4's migration).

4. **Single migration: pre-seed lookups + jurisdiction entries; schema-wide `jurisdiction_id` text → ULID FK refactor; drop `Role.district`.** Migration `2026_06_03_jurisdictional_ia_refactor.py`. Steps within the migration: (a) create new cache tables (4 of them); (b) seed `jurisdiction_types` with 16 rows + `jurisdiction_relationship_types` with 11 codes; (c) seed `clearinghouse_core.jurisdictions` from the bundled `initial_jurisdictions.json` (101 entries) plus the relationships (101 entries); (d) add `jurisdiction_id_new` ULID nullable FK to every ~30 canonical tables; (e) backfill `jurisdiction_id_new` via `UPDATE ... SET jurisdiction_id_new = (SELECT id FROM clearinghouse_core.jurisdictions WHERE slug = jurisdiction_id_old)`; (f) drop `jurisdiction_id_old`, rename `jurisdiction_id_new` → `jurisdiction_id`, mark NOT NULL; (g) drop `Role.district`. Single transaction. **Verifiable when:** common gates pass; `uv run alembic upgrade head` succeeds on the live DB; `uv run alembic check` reports no drift; `clearinghouse_core.jurisdictions` has 101 rows matching the pre-seed.

5. **Update all test fixtures that seed `jurisdiction_id: str` or `Role.district`.** ✅ Completed 2026-06-02 as part of step 4 (the migration's schema changes immediately broke the existing fixtures; folding the sweep into the same commit kept the workspace coherent). Workspace `usa_wa` fixture added in `conftest.py`; 68 `jurisdiction_id="usa-wa"` literals bulk-swept to `jurisdiction_id=usa_wa.id`; `Role(district=...)` kwargs removed. `test_engine` setup now wipes schemas at session start so the fixture chain is independent of any prior `alembic upgrade head` against `TEST_DATABASE_URL`. **Verifiable when:** common gates pass at maintained coverage. ✅ 37 default tests + 1 integration test green at 94.92% coverage; ruff clean.

6. **Update the v1.4 docs sweep:** ✅ Completed 2026-06-02.
   - `docs/specs/2026-05-27-hybrid-legislative-ia.md` — v1.4 changelog row added at the top; `canonical.roles` table entry rewritten (district column dropped, natural-key UQ now `(jurisdiction_id, organization_id, name)`); example tuples updated to show `jurisdiction=<usa-wa-ld-21>` shape; cross-cluster summary annotated; `Person.current_district` removed-note carries forward v1.4 update.
   - `docs/specs/2026-05-27-transformation-legiscan.md` — LegiScan `district` mapping row rewritten to `Role.jurisdiction_id` resolution path; v0 Person baseline note annotated.
   - `docs/specs/2026-05-27-transformation-ocd.md` — v1-landed promotion row annotated with v1.4 follow-on.
   **Verifiable when:** `grep -RIn '\bRole\.district\b\|district:\s*text\|district="' docs/specs/` returns hits only inside v1.4 removal-context annotations + the jurisdictional-IA design spec's design rationale. ✅ Verified.

7. **Cleanup pass on `docs/specs/2026-05-27-power-map-integration.md`.** ✅ Completed 2026-06-02. Updated lines 80 (#162 row marked SHIPPED with the 3-value uppercase vocab) and 170 (observation response example rewritten with `AUTO_ATTACHED` shape). Cross-linked to the jurisdictional-IA design spec §3 for the implementing decisions. **Verifiable when:** `grep "queued-for-review" docs/` returns hits only inside removal-context annotations. ✅ Verified. Remaining hits: `power-map-integration.md` (#162 row + observation response example, both annotated as removed and cross-linked to design spec §3); `jurisdictional-ia-design.md` (Decision summary table at line 52 + Open Question 6 historical context at line 412).

**Sequencing note (updated 2026-06-01):** Step 1's brainstorm landed and PM shipped both phases. Schema decisions in steps 3–4 are now locked against PM's actual shape (no more "may need revision based on PM feedback"). Steps 2–7 proceed sequentially against the locked design.

**Sidecar follow-up plan (out of scope here):** PM #168 Phase 1 (read) and Phase 2 (write) endpoints exist; sidecar implementation (systemd unit, HTTP client, reconciliation, retry logic, observation-shaped writes, disposition handling) becomes a separate plan written after this one ships. Captured here only as the next-plan reference — do not expand this plan's scope to include sidecar.

## Open questions / risks

- ✅ **Naming alignment with PM's existing `wa` prefix.** Resolved — PM accepted the `usa-` prefix as intentionally distinct from identifier slugs. Two namespaces, two conventions, no collision.
- **`initial_jurisdictions.json` data sources for relationships.** Some relationship metadata (effective dates, percentage weights for overlapping districts) isn't in MVP scope; we'll seed null for those fields and accept the looseness. Identifier values (Census-FIPS / ISO-3166-2 / OCD) come from PM via the sidecar's read flow when local cache needs them.
- **Test refactor blast radius.** Step 5 touches ~10 test functions, each updating multiple fixture rows. Migration of test fixtures from `jurisdiction_id="usa-wa"` to FK-based seeding is mechanical but invasive. If a fixture chain becomes unwieldy, a `seed_usa_wa_jurisdictions(db_session)` helper fixture lives in workspace `conftest.py`.
- **Migration ordering — what if `clearinghouse_core.jurisdictions` already has rows from a prior run?** The pre-seed uses `INSERT ... ON CONFLICT (slug) DO NOTHING` so re-runs are idempotent. The existing minimal `clearinghouse_core.Jurisdiction` rows (if any — from `provenance.py` test fixtures) are dropped and recreated as part of the table-replace.
- **PM admin-import path for `initial_jurisdictions.json`.** PM accepts the bootstrap as a one-time admin import (not observation flow). This is operator-facing coordination — the file is checked into usa-wa's adapter package; hand off the path to PM operator when ready. Sidecar's observation flow then operates against anchored IDs (`AUTO_ATTACHED` from day one).
- ✅ **PM coordination timing.** Resolved — PM shipped both phases ahead of this plan's local work. The sidecar follow-up plan is now unblocked but separate scope.
