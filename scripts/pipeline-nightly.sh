#!/usr/bin/env bash
# Nightly #302 pipeline chain (#311): raw harvests → dbt build → registrar →
# anchor export → publish → serving load → parity probes. ExecStart of
# usa-wa-pipeline.service.
#
# Failure policy, stage by stage:
# - a HARVEST failure is contained (counted, chain continues): the raw store
#   keeps the last good wires and the publish shrink-gate protects downstream.
#   A harvest may also exit 0 while reporting a KNOWN outage it has named in
#   code (sos ACCEPTED_OUTAGES, #333) — that is deliberate: a nightly email
#   nobody can act on is how alerting stops being read. The outage is still in
#   the journal, and the acceptance fails the run once upstream recovers;
# - a BUILD failure aborts (nothing downstream can run without the duckdb);
# - REGISTRAR conflicts (exit 4) are counted, not fatal: the pipeline stays
#   publishable during a triage backlog — yesterday's identity universe with
#   today's attributes, never a guessed identity (spec § registrar);
# - an ANCHOR EXPORT failure is contained: the duckdb keeps the last good
#   `pm_anchors`, so publish skips it unchanged rather than refusing. The
#   anchors only move when the (now frozen) sidecar writes, so a re-export is
#   normally a no-op that mints nothing;
# - PUBLISH refusal (exit 1) is counted — the gate did its job, the catalog
#   still lists the last good versions;
# - a SERVING LOAD failure is counted: the API keeps serving the last good
#   snapshot (the load is one transaction), so this is stale-but-correct;
# - PARITY divergence is counted — observational, runs after publish.
# Any counted failure exits 1 at the end so OnFailure= emails the operator.
#
# Paths are absolute or resolved from the primary checkout (WorkingDirectory):
# raw/ + data/pipeline.duckdb + data/datasets are the documented defaults, and
# dbt resolves --target-path relative to the PROJECT dir, so it is spelled out.
set -u
# Guarded (#302 CR): with no -e, a failed cd would scatter raw/ and data/
# under whatever cwd a by-hand invocation inherited.
cd /home/exedev/usa-wa || exit 1

UV="/usr/local/bin/uv run --frozen --no-sync"
failures=0

for job in usa_wa_adapter_legislature.raw_harvest usa_wa_adapter_pdc.raw_harvest usa_wa_adapter_sos.raw_harvest; do
  if ! $UV python -m "$job"; then
    echo "pipeline-nightly: harvest failed (contained): $job" >&2
    failures=$((failures + 1))
  fi
done

if ! $UV dbt build \
    --project-dir packages/usa-wa-pipeline/dbt \
    --profiles-dir packages/usa-wa-pipeline/dbt \
    --target-path /home/exedev/usa-wa/data/target \
    --log-path /home/exedev/usa-wa/data/dbt-logs; then
  echo "pipeline-nightly: dbt build failed — aborting before registrar/publish" >&2
  exit 1
fi

if ! $UV python -m usa_wa_pipeline.registrar --db data/pipeline.duckdb; then
  echo "pipeline-nightly: registrar reported conflicts/failure (triage; publish continues)" >&2
  failures=$((failures + 1))
fi

# The PM crosswalk seed (#312) as a catalog dataset (#354): read Postgres,
# refresh `pm_anchors` in the duckdb, and let publish deliver it the way it
# delivers every other dataset. Before publish so a re-export lands in the same
# nightly catalog; contained, because a Postgres blip must not cost the other
# datasets their publication.
if ! $UV python -m usa_wa_pipeline.anchor_export --db data/pipeline.duckdb; then
  echo "pipeline-nightly: anchor export failed (contained; last good pm_anchors stands)" >&2
  failures=$((failures + 1))
fi

if ! $UV python -m usa_wa_pipeline.publish \
    --db data/pipeline.duckdb \
    --manifest /home/exedev/usa-wa/data/target/manifest.json; then
  echo "pipeline-nightly: publish refused/failed (last good catalog stands)" >&2
  failures=$((failures + 1))
fi

# The deployment's own projection of what just published (#313). After publish
# so it loads the new catalog; before the probes so a load failure is counted
# beside them rather than discovered by a 200 answering stale rows.
if ! $UV python -m usa_wa_api.serving.load; then
  echo "pipeline-nightly: serving load failed (API still serves the last snapshot)" >&2
  failures=$((failures + 1))
fi

for probe in usa_wa_pipeline.parity_wsl usa_wa_pipeline.parity_pdc usa_wa_pipeline.parity_registry \
             usa_wa_pipeline.parity_spans usa_wa_pipeline.parity_citations; do
  if ! $UV python -m "$probe"; then
    echo "pipeline-nightly: parity divergence: $probe" >&2
    failures=$((failures + 1))
  fi
done

if [ "$failures" -gt 0 ]; then
  echo "pipeline-nightly: $failures stage(s) failed" >&2
  exit 1
fi
