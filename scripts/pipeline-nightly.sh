#!/usr/bin/env bash
# Nightly #302 pipeline chain (#311): raw harvests → dbt build → build warnings →
# registrar → publish → serving load → coverage seed → probes.
# ExecStart of usa-wa-pipeline.service.
#
# The PM anchor export sat between registrar and publish until #314 retired it
# along with the `pm_anchors` dataset it fed.
#
# Failure policy, stage by stage:
# - a HARVEST failure is contained (counted, chain continues): the raw store
#   keeps the last good wires and the publish shrink-gate protects downstream.
#   A harvest may also exit 0 while reporting a KNOWN outage it has named in
#   code (sos ACCEPTED_OUTAGES, #333) — that is deliberate: a nightly email
#   nobody can act on is how alerting stops being read. The outage is still in
#   the journal, and the acceptance fails the run once upstream recovers;
# - a BUILD failure aborts (nothing downstream can run without the duckdb);
# - a BUILD WARNING is counted, not fatal (#412 PR E): `dbt build` exits 0 on a
#   `warn` test, so build_warnings reads run_results.json and fails on it. A
#   vacancy is news, not a defect, so the registrar and publish still run;
# - REGISTRAR conflicts (exit 4) are counted, not fatal: the pipeline stays
#   publishable during a triage backlog — yesterday's identity universe with
#   today's attributes, never a guessed identity (spec § registrar);
# - PUBLISH refusal (exit 1) is counted — the gate did its job, the catalog
#   still lists the last good versions;
# - a SERVING LOAD failure is counted: the API keeps serving the last good
#   snapshot (the load is one transaction), so this is stale-but-correct;
# - a COVERAGE SEED failure is counted: /sources keeps yesterday's claims. The
#   seed reconciles each adapter's declared coverage (#180), which the canonical
#   refreshes did until #412 PR E disabled them;
# - a PROBE failure is counted — observational, runs after publish.
#   registry_coverage (#412 PR B) gates the registry's coverage of the build, and
#   must run AFTER the registrar: a first-seen identity is unregistered until it
#   does. parity_citations gates that every published entity stays citable. The
#   canonical-oracle probes (parity_wsl, _pdc, _registry, _spans) retired with
#   the canonical refreshes in #412 PR E.
# Any counted failure exits 1 at the end so OnFailure= emails the operator.
# Either exit restates every failed stage's last stdout line — the harness
# summary, counters included — as its closing lines: the email carries only
# the last 25 journal lines, and a stage's own summary sits hundreds above
# them (#331 CR 6).
#
# Paths are absolute or resolved from the primary checkout (WorkingDirectory):
# raw/ + data/pipeline.duckdb + data/datasets are the documented defaults, and
# dbt resolves --target-path relative to the PROJECT dir, so it is spelled out.
# $DATA and $DB hold them, so a rehearsal (below) can move all of them at once.
set -u
# Guarded (#302 CR): with no -e, a failed cd would scatter raw/ and data/
# under whatever cwd a by-hand invocation inherited.
# PIPELINE_NIGHTLY_ROOT / PIPELINE_NIGHTLY_UV are test seams (#331 CR 6): the
# suite points them at a tmp dir and a stub `uv`. The unit sets neither.
cd "${PIPELINE_NIGHTLY_ROOT:-/home/exedev/usa-wa}" || exit 1

UV="${PIPELINE_NIGHTLY_UV:-/usr/local/bin/uv run --frozen --no-sync}"
failures=0
failed=()

# PIPELINE_NIGHTLY_REHEARSAL=<dir> — the #135 rollover rehearsal's scratch mode
# (scripts/rollover-rehearsal.sh sets it). The chain is the real one, but every
# path it writes moves under <dir>, and the three stages that write state no
# scratch root holds are skipped: the registrar (the registry), the serving
# load (the API's tables) and the coverage seed (/sources). The run ledger is
# the fourth such writer, so USA_WA_JOB_LEDGER=0 is required. It fails closed
# before the first stage: prod sets USA_WA_RAW_ROOT in /etc/usa-wa/.env, so one
# missed override would harvest into the production raw store.
# PIPELINE_NIGHTLY_SKIP (stage labels) is honoured only in a rehearsal, so a
# stray variable can never talk production out of a stage.
REHEARSAL="${PIPELINE_NIGHTLY_REHEARSAL:-}"
WRITERS=""
SKIP=""
# The production checkout is checked by name, not only as $PWD: rehearsing a worktree's
# code, $PWD is the worktree, and a scratch dir above or inside production would otherwise
# pass with roots that are production's own raw store, catalog and duckdb (CR 1).
PRODUCTION=/home/exedev/usa-wa
if [ -n "$REHEARSAL" ]; then
  REHEARSAL=$(realpath -m "$REHEARSAL")
  # Physical, like every path compared against it (CR 10): after a `cd` through a symlink
  # $PWD is the link, and a resolved root never matched it.
  here=$(pwd -P)
  for checkout in "$here" "$PRODUCTION"; do
    case "$REHEARSAL/" in
      "$checkout"/*)
        echo "pipeline-nightly: rehearsal refusing: $REHEARSAL is inside the checkout $checkout" \
          "(the production checkout is $PRODUCTION)" >&2
        exit 2
        ;;
    esac
  done
  for var in USA_WA_RAW_ROOT USA_WA_PIPELINE_DB USA_WA_DATASETS_ROOT; do
    root=$(realpath -m "${!var:-.}")
    case "$root" in
      "$REHEARSAL"/*) ;;
      *)
        echo "pipeline-nightly: rehearsal refusing: $var=${!var:-} is outside $REHEARSAL" >&2
        exit 2
        ;;
    esac
    for checkout in "$here" "$PRODUCTION"; do
      case "$root/" in
        "$checkout"/*)
          echo "pipeline-nightly: rehearsal refusing: $var=$root is inside the checkout $checkout" \
            "(the production checkout is $PRODUCTION)" >&2
          exit 2
          ;;
      esac
    done
  done
  if [ "${USA_WA_JOB_LEDGER:-}" != 0 ]; then
    echo "pipeline-nightly: rehearsal refusing: USA_WA_JOB_LEDGER must be 0 (the run ledger is shared state)" >&2
    exit 2
  fi
  DATA="$REHEARSAL"
  DB=$(realpath -m "$USA_WA_PIPELINE_DB")
  WRITERS=" usa_wa_pipeline.registrar usa_wa_api.serving.load usa_wa_pipeline.coverage_seed "
  SKIP=" ${PIPELINE_NIGHTLY_SKIP:-} "
else
  DATA=/home/exedev/usa-wa/data
  DB=data/pipeline.duckdb
  # A rehearsal variable leaked into production (CR 5) blinds /health/jobs for every job.
  # Publishing continues — a monitoring loss is no reason to stop the chain — but it is mailed.
  if [ "${USA_WA_JOB_LEDGER:-}" = 0 ]; then
    echo "pipeline-nightly: USA_WA_JOB_LEDGER=0 outside a rehearsal — no job records a run" >&2
    failures=$((failures + 1))
    failed+=("USA_WA_JOB_LEDGER=0 in production: /health/jobs records nothing — remove it from the env files")
  fi
fi

# skipped LABEL — true, and says so, when a rehearsal leaves LABEL out.
skipped() {
  case "$WRITERS" in
    *" $1 "*) echo "pipeline-nightly: rehearsal skipped $1 (writes shared state)"; return 0 ;;
  esac
  case "$SKIP" in
    *" $1 "*) echo "pipeline-nightly: rehearsal skipped $1 (PIPELINE_NIGHTLY_SKIP)"; return 0 ;;
  esac
  return 1
}

# run_stage LABEL CMD... — run one stage, its stdout streamed as before; on
# failure, keep "LABEL (exit N): <its last stdout line>" for report_failures.
# The last line is held in memory (lastpipe keeps the read loop in this shell):
# a file under /tmp shares / with the raw store, so on a disk-full night it
# would restate nothing or a stale line (CR 8). printf writes the inherited fd;
# never reopen /dev/stdout or /dev/fd/N — under systemd that is the journal
# socket, which open(2) refuses (ENXIO).
shopt -s lastpipe extglob
run_stage() {
  local label=$1 line last="" rc
  shift
  "$@" | while IFS= read -r line || [ -n "$line" ]; do
    printf '%s\n' "$line"
    last=$line
  done
  rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then
    # dbt colors off a TTY too, and journalctl hands the escapes to the email
    # raw: restate plain text (CR 9). The stage's own lines stay as printed.
    last=${last//$'\e['*([0-9;])m/}
    failed+=("$label (exit $rc): ${last:-(no summary on stdout)}")
  fi
  return "$rc"
}

# report_failures — restate each failed stage as the run's closing lines.
report_failures() {
  local line
  for line in "${failed[@]}"; do
    echo "pipeline-nightly: failed stage: $line" >&2
  done
}

for job in usa_wa_adapter_legislature.raw_harvest usa_wa_adapter_pdc.raw_harvest usa_wa_adapter_sos.raw_harvest; do
  skipped "$job" && continue
  if ! run_stage "$job" $UV python -m "$job"; then
    echo "pipeline-nightly: harvest failed (contained): $job" >&2
    failures=$((failures + 1))
  fi
done

if ! run_stage "dbt build" $UV dbt build \
    --project-dir packages/usa-wa-pipeline/dbt \
    --profiles-dir packages/usa-wa-pipeline/dbt \
    --target-path "$DATA/target" \
    --log-path "$DATA/dbt-logs"; then
  echo "pipeline-nightly: dbt build failed — aborting before registrar/publish" >&2
  report_failures
  exit 1
fi

if ! run_stage usa_wa_pipeline.build_warnings $UV python -m usa_wa_pipeline.build_warnings \
    --run-results "$DATA/target/run_results.json"; then
  echo "pipeline-nightly: dbt build warned (publish continues)" >&2
  failures=$((failures + 1))
fi

if ! skipped usa_wa_pipeline.registrar \
    && ! run_stage usa_wa_pipeline.registrar $UV python -m usa_wa_pipeline.registrar --db "$DB"; then
  echo "pipeline-nightly: registrar reported conflicts/failure (triage; publish continues)" >&2
  failures=$((failures + 1))
fi

if ! run_stage usa_wa_pipeline.publish $UV python -m usa_wa_pipeline.publish \
    --db "$DB" \
    --manifest "$DATA/target/manifest.json"; then
  echo "pipeline-nightly: publish refused/failed (last good catalog stands)" >&2
  failures=$((failures + 1))
fi

# The deployment's own projection of what just published (#313). After publish
# so it loads the new catalog; before the probes so a load failure is counted
# beside them rather than discovered by a 200 answering stale rows.
if ! skipped usa_wa_api.serving.load \
    && ! run_stage usa_wa_api.serving.load $UV python -m usa_wa_api.serving.load; then
  echo "pipeline-nightly: serving load failed (API still serves the last snapshot)" >&2
  failures=$((failures + 1))
fi

if ! skipped usa_wa_pipeline.coverage_seed \
    && ! run_stage usa_wa_pipeline.coverage_seed $UV python -m usa_wa_pipeline.coverage_seed; then
  echo "pipeline-nightly: coverage seed failed (/sources keeps yesterday's claims)" >&2
  failures=$((failures + 1))
fi

for probe in usa_wa_pipeline.registry_coverage usa_wa_pipeline.parity_citations; do
  if ! run_stage "$probe" $UV python -m "$probe" --db "$DB"; then
    echo "pipeline-nightly: probe failed: $probe" >&2
    failures=$((failures + 1))
  fi
done

if [ "$failures" -gt 0 ]; then
  echo "pipeline-nightly: $failures stage(s) failed" >&2
  report_failures
  exit 1
fi
