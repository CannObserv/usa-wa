---
title: Retire the canonical tier (#412)
date: 2026-09-24
status: draft
---

# Retire the canonical tier (#412)

## Problem

The #302 pipeline publishes every dataset, but the Postgres canonical tier still runs seven
daily timers and a weekly one. Two systems that can disagree can also both page: the 2026-09-22
parity alert fired on a one-day skew between them. #412's body treats canonical as the parity
oracle and nothing more. The 2026-09-24 inventory found more couplings than that, and each one
blocks a deletion:

| Coupling | Where | Blocks |
|---|---|---|
| The nightly `dbt build` reads `canonical.operator_events` (534 live) | `operator_read.py`, `dbt/models/conformed/assignments.py:39` | schema drop |
| Operator attestations are written to Postgres `raw_payloads`, not the raw store (4 since the 09-03 export) | `operators/store.py` | dropping provenance |
| `canonical.committee_succession_events` (165) and the `active` flag feed lineage checks the pipeline never ported | `committees/lineage_invariants.py` | schema drop, and a product decision (Q2) |
| Unported checks: chamber counts 49/98, one person on two seats, misdated spans (#272), House and Senate odd-year corroboration, lineage INV1/INV2 | four units | retiring units |
| The pipeline imports DB-writing modules transitively: `conformed/spans` → `roster_pdf/build` → `span_emit`, `operators.store`; `conformed/house` → `facts_seats/house/build`; `conformed/roles` → `normalize/members` → `.adapter`, `.jurisdictions`; the job harness → `models` → `provenance`, `jurisdictions` (registration only; both survive trimmed) | usa-wa-pipeline, clearinghouse-core | deleting modules |
| `/sources` routes read `sources` + `source_coverage`; `sources.jurisdiction_id` FKs `jurisdictions` | `usa_wa_api/api/v1/ops.py` | dropping provenance and jurisdictions whole |
| `source_coverage.evidence_citation_id` FKs `citations`, and `SourceCoverageOut` publishes it (populated in 0 of 5 rows) | `source_coverage.py:246`, `v1/schemas.py:218` | dropping `citations` (Q5) |
| Not every nightly probe needs canonical: `parity_citations` reads only the duckdb, and four of `parity_spans`' zero-gated counters (`unregistered_spans`, `unregistered_orgs`, `unregistered_roles`, `malformed_roster_rows`) have no dbt equivalent — `roles.org_entity_id` is untested, `roles.entity_id` is only `unique`, and unregistered spans drop silently at the inner join | `parity_citations.py`, `parity_spans.INTEGRITY_COUNTERS` | retiring the parity stage whole |
| The file integrity sweep exists but nothing runs it (idle since 09-03) | `clearinghouse_core.raw_integrity` | retiring the Postgres sweep |
| 15 payloads fetched after the 09-03 export exist only in Postgres (6 WSL, 5 PDC, 4 operator) | `raw_payloads` | dropping provenance |
| The roster PDF has no raw-store writer, and its monthly re-check still opens Postgres provenance tables | #421 | dropping provenance |

## Approach

Retire the tier in six PRs, strictly ordered, so that no step removes something a later
step still reads. Everything up to the last PR is reversible.

1. Move curated state off canonical.
2. Port every check the conformed tier can express as a dbt test.
3. Wire the file integrity sweep.
4. Cut the import graph, with an import-linter contract as the fitness function.
5. Disable the timers and soak for a week.
6. Delete the code and drop the tables in one migration, behind a `pg_dump`.

What survives:
- `Source` and `SourceCoverage`, for `/sources` and #180 coverage-as-data
- `job_runs` and the `registry` schema, which gains the curated tables
- `serving`
- a trimmed `jurisdictions` table seeded from `usa_wa_common` (Q4)

## Tradeoffs / alternatives

- **One PR, the issue's original shape.** Rejected: it spans every package, and the destructive half would be reviewed tangled with a refactor.
- **Keep canonical read-only as an archive.** Rejected: the oracle is already stale (baseline 785). It shares Layer 2 with the pipeline, so it cannot catch the rollover bugs that matter (#282 hits both tiers). The `pg_dump` and the immutable dataset versions cover the archive need.
- **Drop the unported checks instead of porting.** Rejected for every check the conformed tier can express. Each check that is dropped gets a recorded reason (Q3).
- **Retire the parity stage whole, as #412's body says.** Rejected: `parity_citations` has no oracle to lose, and `parity_spans`' oracle-free counters are the only nightly alarm for data silently falling out of the published tables (the #403 class).
- **Operator events as git-tracked files.** Viable: see Q1.

## Steps

1. **PR A: curated state off canonical.**
   - Move `operator_events` and `committee_succession_events` to the Q1 home (recommended: `registry`, via `ALTER TABLE … SET SCHEMA`). Update the models, grants and `operator_read` to match.
   - Make `operators.store` record attestation bodies through `RawStore.record_fetch`.
   - Run `raw_export` once to carry the 15 post-export payloads, then run the file sweep.
   - Done when the nightly is green reading operator events from their new home, and the raw store holds a `usa_wa_operator` run newer than 09-03.
   - #421 lands in parallel and must leave the roster harvest, including the re-check, with no Postgres provenance writes before PR F.
2. **PR B: port the checks as dbt tests on the conformed tier.**
   - Chamber counts on the open cohort: **error above** 49/98, **warn below**. Today's `count_ok` is strict equality (`invariants.py:87-88`), and a failed dbt test aborts the nightly before registrar and publish, so a straight port would block publishing on every legitimate vacancy and on the 2027-01-01 rollover. A vacancy is a real state; an excess is a defect.
   - One person on two seats in a chamber.
   - The #272 misdating predicate.
   - House and Senate odd-year winner corroboration, `stg_sos_results` ⋈ `assignments`.
   - Lineage, per Q2.
   - The oracle-free `parity_spans` counters: no span dropped for want of a registered person, no role without a registered org, no malformed roster row — **error**; a role without its own registered entity — **warn**, since it is one build behind the registrar by design.
   - Done when each test is green against the production duckdb, and the Postgres units still run.
3. **PR C: wire the file sweep.** Repoint `usa-wa-integrity-sweep.service` at `clearinghouse_core.raw_integrity` and keep the weekly timer. Done when a scheduled run lands in the ledger.
4. **PR D: cut the import graph.**
   - Extract the pure functions the pipeline uses from `roster_pdf/build`, `facts_seats/house/build`, `normalize/members` and the adapter modules that staging imports.
   - Add a forbidden contract so neither `usa_wa_pipeline` nor `usa_wa_api` can import the runner, `adapter`, `span_emit`, `operators.store`, `bootstrap`, or any refresh/build module.
   - Done when `lint-imports` enforces the contract, and a scratch publish matches that night's catalog digests byte for byte.
5. **PR E: stop the write path.** All of this is reversible:
   - Disable nine units: the WSL, PDC and SOS refreshes, both archive refreshes, succession invariants, committee lineage invariants, and House and Senate corroboration. Keep the unit files. The integrity sweep unit stays: PR C already repointed it.
   - Remove `usa-wa-pipeline.service`'s `After=` on the refreshes, and update the expected edges `test_unit_ordering` pins for it (the file's other guards stay).
   - Remove the oracle-backed probes from `pipeline-nightly.sh`: `parity_wsl`, `parity_pdc`, `parity_registry`, `parity_spans`. **Keep `parity_citations`**: it checks the built artifact, not canonical, and is the only gate that every published entity stays citable.
   - Done when 7 consecutive nightlies are green.
6. **PR F: delete and drop.**
   - Run `raw_export` a final time, now that PR E has stopped every Postgres writer, then the file sweep; only then take a `pg_dump` of `canonical` and the provenance tables. PR A's export cannot be the last one: the refreshes and archive units keep writing `raw_payloads` until PR E.
   - Remove the Postgres-tier modules, the four oracle-backed `parity_*` probes (not `parity_citations`), `registry_seed`, `runner.py`, `adapter.py` and `span_emit`.
   - Remove the canonical identity models and the PM-mirror half of `jurisdictions.py`.
   - Cut `provenance.py` down to `Source` + `SourceCoverage`, and delete the retired units' files.
   - Trim `clearinghouse_core/models.py`'s side-effect registration to the surviving models. The job harness reaches `provenance` and `jurisdictions` only through it, and both modules survive in trimmed form, so PR D need not touch the harness.
   - Resolve `source_coverage.evidence_citation_id` per Q5 before its target goes.
   - Write one alembic migration that drops the `canonical` schema, `fetch_events`, `raw_payloads`, `citations`, `integrity_sweep_state`, `notes`, `document_identifiers`, both jurisdiction-relationship tables, and every `pm_*` column.
   - In the same commit, update `grants.sql`, `LEGACY_MIGRATION_SCHEMAS`, `test_grants_append_only` and the `test_declared_tier` markers.
   - Done when #412's acceptance holds.
7. **Docs, in PR F.** Update every maintained doc that describes the tier as live, plus the AGENTS.md layer table and ARCHITECTURE.md. The drift gates run in that commit. Then run #413's second pass.

**Timing.** #135's early capture (due 2026-11-03) comes first. PRs A–F then land before the #135 rehearsal (due 2026-12-31), so that the rehearsal runs the final system.

## Open questions / risks

- **Q1: where do operator events live?** Recommend the `registry` schema, beside `adjudications`: human-entered corrections belong together, it takes one `SET SCHEMA`, and the operator CLI does not change. Git-tracked files would be reviewable, but the CLI writes at runtime and attestations need the raw store either way.
- **Q2: committee lineage (#124).** Published `organizations` has carried no `active` flag and no succession since #313, so this is already a product gap. Recommend porting `active` in the bundled 2.1.0 contract bump (#384, #369), gating INV1 on it, and deferring a succession dataset. INV2 waits with it, and `committee_succession_events` moves in PR A so the option stays open.
- **Q3: the Senate corroboration citation writer.** It cites SOS on `valid_from`, and the citations artifact excludes SOS by design. Recommend keeping the check (PR B) and dropping the writer, with that reason recorded.
- **Q4: `sources.jurisdiction_id` is in the API (`SourceOut`).** Recommend keeping a trimmed `jurisdictions` table seeded from `usa_wa_common` as the FK target, rather than changing the contract.
- **Q5: `source_coverage.evidence_citation_id`.** It FKs `citations`, which PR F drops, and `/sources/{slug}/coverage` publishes it as `SourceCoverageOut.evidence_citation_id`. It has never carried a value (0 of 5 rows). Recommend dropping the column and the API field in PR F, with an API.md migration note; keeping the field as always-null is the alternative if removing a field counts as breaking for `/api/v1`'s consumers.
- **Risk: PR D is the only step that could change published bytes by accident.** The digest comparison is its gate.
- **Risk: after PR E nothing re-derives canonical.** A rollback past E means re-running the refreshes, which are idempotent. That holds until PR F drops the tables.
