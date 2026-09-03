# usa-wa-pipeline

The dataset-publication pipeline (#302): a dbt-core + dbt-duckdb project with the
three layers of the replatform spec — `staging/` (one cleaning regime per source,
natural keys only), `matching/` (cross-source link proposals), `conformed/`
(registry-joined products). The dbt project lives in `dbt/`; the Python package
exports its location and will grow the publisher.

Build it: `scripts/dbt-gate.sh` (throwaway db), or point `USA_WA_PIPELINE_DB`
somewhere and run `uv run dbt build --project-dir packages/usa-wa-pipeline/dbt
--profiles-dir packages/usa-wa-pipeline/dbt`. TDD policy and layer contracts:
`docs/PIPELINE.md`.
