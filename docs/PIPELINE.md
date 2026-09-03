# The dataset-publication pipeline

`packages/usa-wa-pipeline/` — the dbt-core + dbt-duckdb project at the center of the
#302 replatform (spec: [specs/2026-09-02-dataset-publication-replatform-design.md](specs/2026-09-02-dataset-publication-replatform-design.md)).
This page: layout, commands, and the TDD policy for dbt models. Scaffolded at #303;
each layer's models arrive with its sub-issue (#306–#309).

## Layout

```
packages/usa-wa-pipeline/
  src/usa_wa_pipeline/   — Python surface: PROJECT_DIR now; the publisher later (#311)
  dbt/                   — the dbt project
    dbt_project.yml      — three model layers: staging / matching / conformed
    profiles.yml         — duckdb target; USA_WA_PIPELINE_DB names the db file
                           (default data/pipeline.duckdb relative to the repo root;
                           gate + tests always override with a throwaway path)
    models/staging/      — one cleaning regime per source, NATURAL KEYS ONLY
    models/matching/     — cross-source link proposals feeding the registrar (#308)
    models/conformed/    — registry-joined products with stable ULIDs (#309)
    seeds/               — small checked-in inputs (currently the scaffold smoke seed)
```

Layer rules are the spec's: staging never joins across sources and never sees a ULID;
matching proposes and never writes identity; conformed is a stateless join against the
registry crosswalk. `usa_wa_pipeline` sits beside `usa_wa_facts_seats` in the
import-linter layer order and, like it, may never import an adapter `transport` —
models re-parse the archive, they do not drive wires.

## Commands

```bash
# Build everything + run all schema/data tests against a throwaway db (what the gate runs)
scripts/dbt-gate.sh

# Iterate against a persistent local db
export USA_WA_PIPELINE_DB=data/pipeline.duckdb
uv run dbt build --project-dir packages/usa-wa-pipeline/dbt --profiles-dir packages/usa-wa-pipeline/dbt

# One model + its tests
uv run dbt build --project-dir packages/usa-wa-pipeline/dbt --profiles-dir packages/usa-wa-pipeline/dbt -s stg_scaffold_smoke
```

The pre-commit hook `dbt-build` runs the gate whenever a commit touches
`packages/usa-wa-pipeline/` (pinned by `scripts/tests/test_pipeline_gate.py`). dbt's
`target/` and `logs/` and the local `data/` db are git-ignored; the gate writes its
artifacts into a temp dir so the checkout stays clean.

## TDD for dbt models

Red → Green → Refactor applies; what changes is where each color lives:

- **A model's contract is its schema entry.** Before writing `stg_x.sql`, write the
  `schema.yml` block declaring its columns and data tests (`not_null`, `unique`,
  `accepted_values`, relationship tests). A declared model with no SQL fails `dbt
  build` — that is the red. The SQL that satisfies the tests is the green.
- **Behavior beyond column shape** (a survivorship rule, a dedup, a windowing edge)
  gets a dbt **data test** (`tests/*.sql` — a query that must return zero rows) or a
  seed-driven unit test: check in a minimal input seed + the expected output as a
  seed, and a test selecting the symmetric difference. Write it failing first.
- **dbt Python models** (the span engine, #309) keep their logic in importable,
  pytest-covered functions (`clearinghouse_domain_legislative` stays the home of the
  pure span code); the dbt model is a thin adapter over them. pytest owns the logic's
  red/green; dbt data tests own the wiring's.
- **Never weaken a test to go green.** Same rule as everywhere in this repo; a data
  test that fails on real source data is a finding about the source — record it
  (coverage claim, exclusion with a comment, or an upstream issue), don't delete it.

pytest still owns everything Python: `packages/usa-wa-pipeline/tests/` drives dbt
in-process (`dbtRunner`) and proves the harness end-to-end, including that a violated
data test fails the build.
