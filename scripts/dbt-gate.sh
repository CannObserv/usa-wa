#!/usr/bin/env bash
# Pre-commit gate (#303): `dbt build` the pipeline project against a THROWAWAY
# duckdb, so a commit touching packages/usa-wa-pipeline/ is blocked unless every
# model compiles and every schema/data test passes. Never touches the dev
# database file, and writes dbt's logs/target into the same temp dir so the
# checkout stays clean. Pinned by scripts/tests/test_pipeline_gate.py.
set -euo pipefail
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export USA_WA_PIPELINE_DB="$tmp/gate.duckdb"
# Hermetic: an empty raw root, so the gate validates compilation + schema/data
# tests without parsing the checkout's live store (or fetching WSDLs) (#306).
export USA_WA_RAW_ROOT="$tmp/raw"
uv run --frozen --no-sync dbt build \
  --project-dir packages/usa-wa-pipeline/dbt \
  --profiles-dir packages/usa-wa-pipeline/dbt \
  --target-path "$tmp/target" \
  --log-path "$tmp/logs"
