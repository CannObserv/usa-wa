---
title: Implement Jurisdictional IA — local cache + schema-wide jurisdiction_id FK refactor + PM feature request
date: 2026-05-31
status: draft
---

# Jurisdictional IA — implementation plan

## Problem

`Role.district: text(32)` is an unanchored label; `jurisdiction_id: text(32)` across ~30 canonical tables is a partition tag with no referential integrity, no hierarchy, no temporal validity. The brainstorm-approved design at [`docs/specs/2026-05-31-jurisdictional-ia-design.md`](../specs/2026-05-31-jurisdictional-ia-design.md) extends Power Map with a Jurisdiction entity type (graph + bitemporal; spatial deferred), and refactors usa-wa to consume via a local cache that mirrors PM's shape. This plan lands the usa-wa-side work (which is mostly independent of PM endpoints) and files the upstream feature request so PM can build in parallel.

## Approach

Land the local schema work in one migration: create the four cache tables (`clearinghouse_core.jurisdictions`, `jurisdiction_relationships`, `jurisdiction_relationship_types`, `jurisdiction_identifiers`), seed the relationship-type lookup vocab and the WA-relevant jurisdiction set (39 counties + 49 LDs + 10 CDs + Seattle + state + country), and refactor every `jurisdiction_id: text(32)` column across ~30 canonical tables to `ULID NOT NULL FK`. Drop `Role.district`. Update tests for every fixture that seeded jurisdiction text. The sidecar (read flow + observation-shaped write flow) defers to P1+ / P3+ — blocked on PM read endpoints + PM #162/#164 respectively. File the PM feature request as the last step so PM knows the contract usa-wa is building to.

## Tradeoffs / alternatives

- **Local-only hierarchy table (no PM integration at all)** — rejected because identity (Person / Org / Role / Assignment) already flows through PM via the producer/archival pattern; jurisdictions diverging from PM creates two parallel identity surfaces. Cohort coherence is the win.
- **Defer the full schema-wide FK refactor; ship only Role.jurisdiction_id refactor + the cache tables** — rejected because the partial refactor leaves jurisdiction_id text(32) in place across the rest of the schema, requiring a second migration later. Cleaner to do once while there's no production data to backfill.
- **Wait for PM to ship the Jurisdiction extension before doing any usa-wa work** — rejected because most of the usa-wa work (cache models + schema refactor + pre-seed) is independent of PM endpoints. The sidecar is the only PM-blocked piece, and it's already deferred to P1+ in the design.
- **Build a sibling Jurisdiction-as-a-Service (the JaaS path from the external exploration)** — rejected during the brainstorm; user picked "extend Power Map" over "new sibling service." Re-evaluate if PM declines the feature request.

## Steps

1. **File the upstream CannObserv/power-map feature request EARLY.** ✅ Filed 2026-05-31 as [CannObserv/power-map#168](https://github.com/CannObserv/power-map/issues/168). Body cross-links to the design spec, summarizes Sections 1 + 3, calls out dependencies on #162 + #164, and lists three specific asks (slug convention, `type` vocabulary, relationship-type vocabulary) plus five open questions inviting PM feedback. Framed as a design conversation, not a locked feature request. **Reordered from step 7 (2026-05-31)** — early filing surfaces PM-side feedback while local work is still cheap to revise.

2. **Produce `initial_jurisdictions.json` proposal + obtain user review approval.** ✅ Completed 2026-06-01. File at `packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/data/initial_jurisdictions.json`. Final tally: 101 jurisdictions (1 country + 1 state + 49 LDs (generic `legislative_district` type) + 10 CDs + 39 counties + Seattle) and 101 relationships (the 100 state-level + country-level containments plus Seattle additionally contained by King County as a worked example of the city-in-county graph relation). Naming conventions verified against Section 1 slug rules; multi-word county names use `_` substitution (`grays_harbor`, `pend_oreille`, `san_juan`, `walla_walla`).

3. **Add the four cache models in `clearinghouse_core`.** `clearinghouse_core.jurisdictions` (replaces the existing minimal one — `JurisdictionLevel` StrEnum drops, `type` becomes free-text), `jurisdiction_relationship_types` (lookup with the 12 codes + `symmetric` flag + `category`), `jurisdiction_relationships` (bitemporal junction), `jurisdiction_identifiers` (1:N polymorphic IDs). Side-effect imports updated in `clearinghouse_core/__init__.py`. **Verifiable when:** `uv run pytest` passes (no schema regressions); `uv run alembic check` reports no drift; the existing `clearinghouse_core.Jurisdiction` smoke imports still work.

4. **Single migration: pre-seed lookup + jurisdiction entries; schema-wide `jurisdiction_id` text → ULID FK refactor; drop `Role.district`.** Migration `2026_06_03_jurisdictional_ia_refactor.py`. Steps within the migration: (a) create new cache tables; (b) seed `jurisdiction_relationship_types` with 12 codes; (c) seed `clearinghouse_core.jurisdictions` from the bundled `initial_jurisdictions.json` plus the relationships; (d) add `jurisdiction_id_new` ULID nullable FK to every ~30 canonical tables; (e) backfill `jurisdiction_id_new` via `UPDATE ... SET jurisdiction_id_new = (SELECT id FROM clearinghouse_core.jurisdictions WHERE slug = jurisdiction_id_old)`; (f) drop `jurisdiction_id_old`, rename `jurisdiction_id_new` → `jurisdiction_id`, mark NOT NULL; (g) drop `Role.district`. Single transaction. **Verifiable when:** `uv run alembic upgrade head` succeeds on the live DB; `uv run alembic check` reports no drift; `clearinghouse_core.jurisdictions` has 100 rows matching the pre-seed.

5. **Update all test fixtures that seed `jurisdiction_id: str` or `Role.district`.** Touches every test that creates a canonical entity with `jurisdiction_id="usa-wa"`. Pattern: seed a local cache row (or fetch from the test fixture chain) and use its ULID. Same change everywhere — mechanical refactor. Adapter-shape tests stay unchanged (no FKs in adapter scaffolding). **Verifiable when:** `uv run pytest` passes all 30+ tests at maintained coverage.

6. **Update `docs/specs/2026-05-27-hybrid-legislative-ia.md`** with the v1.4 changelog row documenting the schema-wide jurisdiction_id refactor + Role.district drop + cache table additions. Cross-link to this plan and the design spec. **Verifiable when:** the hybrid IA spec reflects the new schema state.

7. **Cleanup pass on `docs/specs/2026-05-27-power-map-integration.md`.** Replace `queued-for-review` references with the current 3-disposition vocab (`auto-attached` / `new` / `rejected`). Captured as Open Question 6 in the design spec. **Verifiable when:** `grep "queued-for-review" docs/` returns no hits.

**Sequencing note:** Steps 2–7 can run while step 1 is in PM-side brainstorm. Schema decisions in steps 3–4 may need revision based on PM feedback — if PM proposes vocabulary or shape changes during the brainstorm, edit the design spec (and this plan) before landing the migration in step 4.

## Open questions / risks

- **Naming alignment with PM's existing `wa` prefix.** PM uses `wa` in identifier slugs; the new Jurisdiction slugs use `usa-wa`. Two parallel conventions inside PM after this lands. Flagged in design spec Open Question 5; resolution depends on PM team's response to the feature request.
- **`initial_jurisdictions.json` data sources for relationships.** Some relationship metadata (effective dates, percentage weights for overlapping districts) isn't in MVP scope; we'll seed null for those fields and accept the looseness. The Census-FIPS / ISO-3166-2 / OCD identifier values can be filled in over time as the sidecar's read flow lands and PM ships its records.
- **Test refactor blast radius.** Step 4 touches ~10 test functions, each updating multiple fixture rows. Migration of test fixtures from `jurisdiction_id="usa-wa"` to FK-based seeding is mechanical but invasive. If a fixture chain becomes unwieldy, a `seed_usa_wa_jurisdictions(db_session)` helper fixture lives in workspace `conftest.py`.
- **Migration ordering — what if `clearinghouse_core.jurisdictions` already has rows from a prior run?** The pre-seed uses `INSERT ... ON CONFLICT (slug) DO NOTHING` so re-runs are idempotent. The existing minimal `clearinghouse_core.Jurisdiction` rows (if any — from `provenance.py` test fixtures) are dropped and recreated as part of the table-replace.
- **PM coordination timing.** Step 7 files the feature request; PM's response (estimated timeline, scope confirmation) is the gating signal for the sidecar work in P1+. usa-wa work in this plan is independent — landing it doesn't block on PM.
