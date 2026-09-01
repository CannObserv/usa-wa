#!/usr/bin/env bash
# Once-per-day context-manifest drift report (#300). Reports; never repairs.
# Designed for invocation as a Claude Code SessionStart hook — exits 0 on every
# condition so a failure here never blocks a session.
#
# Sibling of socraticode-health.sh, and deliberately not part of it. That check
# is vendored and inspects what the manifest already NAMES — a stopped
# container, a FAILED index, a declared artifact that never indexed. A doc that
# is simply absent from .socraticodecontextartifacts.json is invisible to it and
# stays invisible: in #298 that hid eleven docs/MODULES-*.md references plus
# ARCHITECTURE.md, ONTOLOGY.md, API.md and DEPLOYMENT.md, all indexable, none
# reachable through codebase_context_search.
#
# The comparison itself lives in scripts/context_manifest_drift.py, which
# scripts/tests/test_context_manifest_drift.py runs as a gate. This file is the
# cadence: the gate catches drift at the commit that introduces it, this catches
# drift that arrives without the suite running.
set -euo pipefail
# -E on its own line, matching the sibling hook: without it the ERR trap is not
# inherited by functions or command substitutions, so the backstop would cover
# only top-level commands — which is not what "any unhandled error exits 0"
# claims.
set -E

_hook_panic() {
  local rc=$?
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] unexpected hook error (rc=$rc)" \
    >> "${LOG:-/dev/null}" 2>/dev/null || true
  exit 0
}
trap _hook_panic ERR

for arg in "$@"; do
  if [[ "$arg" == "--help" ]]; then
    cat <<USAGE
Usage: bash .claude/hooks/context-manifest-drift.sh [--help]

Once-per-day report of tracked docs missing from the SocratiCode context
manifest. Designed for invocation as a Claude Code SessionStart hook — never
blocks a session.

What it reports (to stdout, which Claude Code injects as session context):
  - tracked Markdown at the repo root or under docs/ that no declared artifact
    covers, so codebase_context_search cannot reach it;
  - entries in .skills/context-artifacts-exempt that name a file which is gone,
    or one that has since been declared. An opt-out nobody revisits is a
    blindfold.

Behaviour:
  - Measures the PRIMARY CHECKOUT, not the session's cwd — SocratiCode indexes
    by absolute project path, and the manifest that matters is the one that
    checkout carries. Resolved as the parent of --git-common-dir (#180).
  - Runs at most once per UTC day, per PROJECT — the lock lives in the common
    git dir, so N worktrees of one repo produce one report a day, not N.
  - Silent when clean, and on every condition it cannot judge (no manifest, no
    checker, no python).
  - Logs to <common .git>/context-manifest-drift.log (~64 KiB / 200 lines).
  - Exits 0 on every condition.

Env:
  CONTEXT_MANIFEST_DRIFT_FORCE=1   ignore the once-per-day lock (for testing)

Options:
  --help    Show this help and exit.

Exit codes:
  0  Always (this hook never blocks a session).
USAGE
    exit 0
  fi
done

git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# The COMMON git dir, not this checkout's private one: --git-common-dir yields
# the shared .git for a worktree and the primary checkout alike, and its parent
# is the checkout SocratiCode indexed. --path-format=absolute needs git >= 2.31;
# without it the value is relative to cwd in the primary checkout.
commondir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || commondir=""
if [ -z "$commondir" ]; then
  commondir="$(git rev-parse --git-common-dir 2>/dev/null)" || exit 0
  case "$commondir" in /*) ;; *) commondir="$PWD/$commondir" ;; esac
fi
LOCK="$commondir/context-manifest-drift.lock"
LOG="$commondir/context-manifest-drift.log"
PROJECT="$(dirname "$commondir")"

# Nothing to check where SocratiCode never ran, and nothing to check with if the
# checker is absent — a worktree with uninitialized submodules still has both,
# since neither is vendored. Probed at $PROJECT, the path about to be measured:
# a layout where dirname(commondir) is not a checkout (a bare repo's worktree)
# has neither, so the hook stays silent rather than measuring a guess.
CHECKER="$PROJECT/scripts/context_manifest_drift.py"
[ -f "$PROJECT/.socraticodecontextartifacts.json" ] || exit 0
[ -f "$CHECKER" ] || exit 0

if [ "${CONTEXT_MANIFEST_DRIFT_FORCE:-0}" != "1" ] \
  && [ -f "$LOCK" ] \
  && [ "$(cat "$LOCK" 2>/dev/null || true)" = "$(date -u +%Y%m%d)" ]; then
  exit 0
fi

if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt 65536 ]; then
  if tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null; then
    mv -f "$LOG.tmp" "$LOG" 2>/dev/null || rm -f "$LOG.tmp"
  fi
fi

_log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >>"$LOG" 2>/dev/null || true
}

# Stamped BEFORE the check, like the sibling hooks: a transient failure must not
# re-run and re-report on every same-day session. Reported when it fails, never
# fatal — the lock lives in the COMMON git dir, so a silent failure to write it
# re-reports in every checkout of the repo rather than in one.
if ! date -u +%Y%m%d > "$LOCK" 2>/dev/null; then
  _log "could not stamp $LOCK — the once-per-day guard is off until this is fixed"
fi

# The project's own venv first: it is the interpreter the suite runs the same
# checker under. The checker needs nothing beyond the standard library, so a
# bare python3 is a fine fallback and no venv is a skip, not a finding.
PYTHON=""
for candidate in "$PROJECT/.venv/bin/python" python3 python; do
  if [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
[ -n "$PYTHON" ] || { _log "no python interpreter found — skipped"; exit 0; }

FINDINGS=""
RC=0
FINDINGS="$("$PYTHON" "$CHECKER" --project-root "$PROJECT" 2>>"$LOG")" || RC=$?

# rc 2 is "the check did not run" — a malformed manifest, an unreadable list.
# Reported, because silence here is byte-identical to a clean tree, and a miss
# that prints like a pass is the failure this whole check exists to close.
if [ "$RC" -eq 2 ]; then
  echo "context-manifest-drift: the check could not run (see $LOG); manifest coverage is unverified today."
fi

if [ "$RC" -eq 1 ] && [ -n "$FINDINGS" ]; then
  echo "context-manifest-drift: docs missing from .socraticodecontextartifacts.json (see $LOG):"
  echo "$FINDINGS"
  echo "context-manifest-drift: declare each one, or list it in .skills/context-artifacts-exempt."
  echo "context-manifest-drift: this hook reports only — re-index with codebase_index after declaring."
fi

_log "checker exited $RC"
[ -n "$FINDINGS" ] && _log "$FINDINGS"

exit 0
