---
title: P0 — workspace + Postgres + adapter contract + discovery tracks
date: 2026-05-26
status: draft
---

# P0 — foundation + discovery tracks

Tracks the spec at [docs/specs/2026-05-25-usa-wa-mvp-design.md](../specs/2026-05-25-usa-wa-mvp-design.md). GitHub tracking issue: [#2](https://github.com/CannObserv/usa-wa/issues/2). Scope is the P0 row of the phasing table.

## Problem

Nothing in P1a–P1c can start until the workspace is split per the 4-layer architecture, Postgres is provisioned on the VM, and the `BaseAdapter`/`AdapterRunner` contract exists in `clearinghouse-core`. Three discovery tracks (Archiver, Watcher, multi-state legislative IA) must also produce written outputs that pressure-test the Layer 2 domain shape before P1a normalization code is written — otherwise we risk locking in a Layer 2 schema that doesn't survive contact with a second jurisdiction.

## Approach

Convert this repo's single-package layout to a `uv` workspace whose members are `clearinghouse-core`, `clearinghouse-domain-legislative`, `usa-wa-adapter-legislature`, and `usa-wa-api` (the other two WA adapters scaffold when their phases start). Move existing `src/api/` and `src/core/` into `packages/usa-wa-api/src/usa_wa_api/` and into `packages/clearinghouse-core/src/clearinghouse_core/` respectively, keeping the systemd-deployed app entrypoint stable. Provision Postgres on the VM with dedicated dev and test databases. Implement `clearinghouse-core`'s provenance models (Jurisdiction, Source, FetchEvent, RawPayload, Citation, ULID column type) and the abstract adapter contract; ship a single empty adapter package skeleton (`usa-wa-adapter-legislature`) to validate the Layer 3 shape end-to-end. Run the three discovery tracks in parallel with the code work; their outputs land as committed Markdown research notes under `docs/research/`.

## Tradeoffs / alternatives

- **Defer the workspace conversion until P1a.** Rejected — every adapter package added later assumes the workspace layout exists; doing it once now is cheaper than retrofitting it under feature pressure.
- **Scaffold all three adapter packages in P0 (legislature, pdc, rcw).** Rejected — only one is needed to validate the Layer 3 shape; the others get created when P1b/P1c start, against a then-stable contract.
- **Skip the discovery tracks and start P1a immediately.** Rejected — the spec elevates these to gates because Layer 2 mistakes are expensive; a week of reading is cheaper than an entity-model migration.
- **Split P0 into two plans (foundation + discovery).** Rejected — added planning overhead doesn't buy clarity; the steps are already independent and a single plan keeps reviewers focused on one artifact.
- **Use BINARY(16) for ULID storage.** Open — decided in step 3; see open questions.

## Steps

1. **Convert to `uv` workspace.** Edit root `pyproject.toml` to define the workspace, create `packages/clearinghouse-core/`, `packages/clearinghouse-domain-legislative/`, `packages/usa-wa-adapter-legislature/`, `packages/usa-wa-api/`. Move existing `src/api/` → `packages/usa-wa-api/src/usa_wa_api/api/`; move existing `src/core/` modules into `packages/clearinghouse-core/src/clearinghouse_core/` (database, logging, config; `models.py` stays empty for now). Update the systemd unit's `ExecStart` to point at `usa_wa_api.api.main:app`. Update `alembic/env.py` import paths. Verify: `uv sync` succeeds, `ruff check .` passes, `uv run pytest --no-cov tests/test_health.py` runs (may fail-with-503 until Postgres lands; the test itself should at least *load*).

2. **Provision Postgres on the VM.** Install via `apt`, create the `usa_wa` cluster user, dev DB (`usa_wa_dev`), and test DB (`usa_wa_test`). Add `DATABASE_URL` to `/etc/usa-wa/.env` and `TEST_DATABASE_URL` to `./.env`. Per the `init-project-fastapi/references/postgres-provisioning.md` reference (vendored skill). Verify: `psql "$DATABASE_URL" -c '\dn'` works; `sudo systemctl restart usa-wa` brings up the live service with `/ready` returning 200.

3. **Decide ULID storage representation.** Compare `BINARY(16)` and `text(26)` for psql ergonomics, B-tree behavior, and asyncpg interop. Document the decision in a one-paragraph ADR-style note inside `packages/clearinghouse-core/src/clearinghouse_core/db/ulid.md`. Verify: the chosen representation is referenced by the SQLAlchemy column type in step 4.

4. **Implement `clearinghouse-core` provenance + identity primitives.** Define the `ULID` SQLAlchemy `TypeDecorator`, the declarative `Base`, and SQLAlchemy models for `Jurisdiction`, `Source`, `FetchEvent`, `RawPayload`, and `Citation` (polymorphic `(entity_type, entity_id)`). Add SQLAlchemy session factory + engine helpers (moved from old `src/core/database.py`). Verify: `uv run pytest packages/clearinghouse-core/tests/` covers ULID round-trip + Citation polymorphic insert.

5. **Implement `BaseAdapter` + `AdapterRunner` in `clearinghouse-core`.** `BaseAdapter` ABC with `source_name`, `schema_name`, `jurisdiction_id` ClassVars and the three abstract methods (`fetch_one`, `discover`, `normalize`). `AdapterRunner` owns the cache-or-fetch logic, writes `FetchEvent`/`RawPayload` rows, writes `Citation` rows from `NormalizedBatch`, and exposes `fetch_and_normalize()` + `refresh()`. Use an in-memory `BaseAdapter` subclass in tests (no real source). Verify: `uv run pytest` covers cache-hit short-circuit, cache-miss refetch, idempotent upsert on `(source, source_id)`, and provenance rows written on success.

6. **First `alembic` migration: `clearinghouse_core` schema.** Write `2026_05_XX_clearinghouse_core_init.py` creating `clearinghouse_core.jurisdictions`, `.sources`, `.fetch_events`, `.raw_payloads`, `.citations` per the spec. Apply against the dev DB; insert one `Jurisdiction` row (`('usa-wa', 'Washington State', 'state')`) via a seed step. Verify: `uv run alembic upgrade head` succeeds; `psql -c "select id, slug, name from clearinghouse_core.jurisdictions;"` returns the one row.

7. **`clearinghouse-domain-legislative` entity skeleton.** Define SQLAlchemy models for the legislative-domain entities listed in the spec (Bill, Legislator, BillSponsorship, BillAction, BillVersion, Committee, Hearing, StatuteCode, StatuteTitle, StatuteChapter, StatuteSection, BillStatuteChange, Filer, LobbyingActivity, LobbyingPosition, Contribution) with `jurisdiction_id` on every table. No alembic migration yet — schema applied in P1a once IA-research findings (step 10) are incorporated. Verify: `uv run pytest packages/clearinghouse-domain-legislative/tests/` covers a smoke import + a single-entity instantiation test per cluster (Bill/Statute/Filer).

8. **Scaffold `usa-wa-adapter-legislature`.** `pyproject.toml` depending on `clearinghouse-core` and `clearinghouse-domain-legislative`; a `WALegislatureAdapter(BaseAdapter)` shell with `NotImplementedError` stubs; a `tests/` directory with one test asserting the subclass conforms to `BaseAdapter`'s ABC. No SOAP client yet (P1a). Verify: `uv run pytest packages/usa-wa-adapter-legislature/tests/` passes.

9. **Discovery track A — Archiver integration contract.** Read `github.com/CannObserv/archiver` (README, schema, any API docs). Write `docs/research/2026-05-26-archiver-integration-contract.md` covering: (a) what URLs/payloads Archiver stores, (b) retrieval interface, (c) push-from-usa-wa vs poll-by-archiver model, (d) recommended MVP integration posture (tight push, loose URL-ref-only, or hybrid), (e) Source.cache_ttl_days interaction. Verify: document exists, ends with a single recommended posture, identifies any blocking unknowns.

10. **Discovery tracks B and C in parallel.**
    - **B (Watcher):** read `github.com/CannObserv/watcher`. Write `docs/research/2026-05-26-watcher-integration-contract.md` covering scheduling capability, push/pull semantics, and a P2-evaluation recommendation on whether usa-wa runs APScheduler indefinitely or migrates to Watcher.
    - **C (Multi-state IA):** survey OpenStates (`github.com/openstates/openstates-core`), LegiScan API docs, GovTrack-derived schemas (`github.com/unitedstates/congress`), and NCSL legislative-information resources. Write `docs/research/2026-05-26-multi-state-legislative-ia-delta.md` containing a per-source delta table against the step-7 Layer 2 entities (Bill, Legislator, BillAction, BillSponsorship, StatuteSection, Filer) and a recommendation block (entity/field additions or renames to apply before P1a normalization lands).

    Verify: both documents exist; each ends with a concrete recommendation block; any blocking schema revisions to step 7 are filed as edits to the spec and to `clearinghouse-domain-legislative` before P1a starts.

## Open questions / risks

- **ULID storage** — chosen in step 3; the spec lists this as still-open. Either choice is reversible with a migration but cheap to get right the first time.
- **Alembic env strategy when packages own their own metadata.** Single root `alembic/env.py` imports `clearinghouse_core.Base.metadata` and `clearinghouse_domain_legislative.Base.metadata` (and later, adapter-package metadata). If autogen-detection produces noise across packages, may need a per-package metadata-merge helper. Defer to first sign of pain.
- **Discovery output format.** Markdown research notes under `docs/research/` are sufficient for now; if findings get long enough to warrant their own spec, promote to `docs/specs/`.
- **Risk: IA research surfaces a schema change late.** If step 10C surfaces a substantive Layer 2 revision after step 7 is in, the spec and step 7 entities both need editing. Acceptable cost; the alternative (delaying step 7 until step 10 finishes) blocks step 8 unnecessarily.
- **Risk: workspace move breaks pre-commit / CI assumptions.** Pre-commit ruff hook config and `pyproject.toml` `tool.pytest.ini_options` likely need updates. Catch in step 1.
- **Risk: systemd `ExecStart` path change requires a service restart at step 1.** Brief downtime on port 8000. Acceptable; coordinate with anyone using the live service.
