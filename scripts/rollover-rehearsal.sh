#!/usr/bin/env bash
# Biennium-rollover rehearsal (#135 item 2): the REAL nightly chain, run under the next
# biennium against scratch copies of the production raw store and catalog.
#
#   scripts/rollover-rehearsal.sh empty   [keep]   # live harvests: whatever upstream serves today
#   scripts/rollover-rehearsal.sh partial [keep]   # no harvests; a synthesized sponsors:<next>
#                                                  # wire, `keep` (default 0.6) of the outgoing
#                                                  # biennium's members (scripts/rehearsal_roster.py)
#
# It prints the scratch dir; the chain's whole output is <scratch>/nightly.log, and the built
# duckdb, dbt run_results and published catalog stay there for reading. Exit = the chain's.
#
# Isolation is pipeline-nightly.sh's rehearsal mode, which refuses to start unless every root
# lies in the scratch dir: it skips the registrar, the serving load and the coverage seed, and
# this script sets USA_WA_JOB_LEDGER=0 so no run reaches /health/jobs. What it still READS from
# production: the registry crosswalk and operator events (DATABASE_URL), because the build
# joins them — read-only, as every nightly does.
#
# The code that runs is this checkout's (so a worktree rehearses its branch); the data copied
# is the production checkout's. Seams for the suite, unset in real use:
# ROLLOVER_REHEARSAL_SOURCE (production checkout), ROLLOVER_REHEARSAL_ENV_FILES (the env files
# loaded before the overrides), ROLLOVER_REHEARSAL_DIR (the scratch dir), and
# ROLLOVER_REHEARSAL_BIENNIUM (default 2027-28).
set -euo pipefail

scenario=${1:-}
keep=${2:-0.6}
case "$scenario" in
  empty | partial) ;;
  *)
    echo "usage: rollover-rehearsal.sh empty|partial [keep-fraction]" >&2
    exit 2
    ;;
esac

code_root=$(cd "$(dirname "$0")/.." && pwd)
source_root=${ROLLOVER_REHEARSAL_SOURCE:-/home/exedev/usa-wa}
biennium=${ROLLOVER_REHEARSAL_BIENNIUM:-2027-28}
start=${biennium%%-*}
outgoing="$((start - 2))-$(printf '%02d' $(((start - 1) % 100)))"
scratch=${ROLLOVER_REHEARSAL_DIR:-$HOME/rehearsal/$biennium-$scenario-$(date -u +%Y%m%dT%H%M%SZ)}

# The production env first, as the unit loads it — the build needs DATABASE_URL — then every
# root it names is overridden. /etc/usa-wa/.env sets USA_WA_RAW_ROOT to production's. Each
# KEY=VALUE line is taken literally, as `export $(cat … | xargs)` takes it: never sourced, so
# a `$` or a backtick in a secret is data, not code.
for env_file in ${ROLLOVER_REHEARSAL_ENV_FILES-/etc/usa-wa/.env $source_root/.env}; do
  [ -r "$env_file" ] || continue
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ $line =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      export "$line"
    fi
  done <"$env_file"
done

mkdir -p "$scratch"
echo "$biennium $scenario" >"$scratch/.rehearsal"
cp -a "$source_root/raw" "$scratch/raw"
cp -a "$source_root/data/datasets" "$scratch/datasets"

export USA_WA_BIENNIUM="$biennium"
export USA_WA_RAW_ROOT="$scratch/raw"
export USA_WA_PIPELINE_DB="$scratch/pipeline.duckdb"
export USA_WA_DATASETS_ROOT="$scratch/datasets"
export USA_WA_JOB_LEDGER=0
export PIPELINE_NIGHTLY_REHEARSAL="$scratch"
export PIPELINE_NIGHTLY_ROOT="$code_root"

UV="${PIPELINE_NIGHTLY_UV:-/usr/local/bin/uv run --frozen --no-sync}"
if [ "$scenario" = partial ]; then
  (cd "$code_root" && $UV python scripts/rehearsal_roster.py \
    --root "$scratch/raw" --from "$outgoing" --to "$biennium" --keep "$keep")
  # A live harvest would re-fetch sponsors:<next> and supersede the synthesized wire.
  export PIPELINE_NIGHTLY_SKIP="usa_wa_adapter_legislature.raw_harvest usa_wa_adapter_pdc.raw_harvest usa_wa_adapter_sos.raw_harvest"
fi

echo "rollover-rehearsal: $scenario under $biennium in $scratch"
rc=0
bash "$code_root/scripts/pipeline-nightly.sh" 2>&1 | tee "$scratch/nightly.log" || rc=${PIPESTATUS[0]}
echo "rollover-rehearsal: $scenario exit $rc — scratch $scratch"
exit "$rc"
