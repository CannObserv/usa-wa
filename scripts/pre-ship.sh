#!/usr/bin/env bash
# Project-local pre-ship gate (#172).
#
# Loads usa-wa's two env files (AGENTS.md § Environment Variables), then hands
# off to the vendored gate. Needed because conftest.py raises on a missing
# TEST_DATABASE_URL, so the gate's test phase died on a clean shell with a bare
# ImportError — a gate that fails for non-code reasons trains people to wave it
# through. This is the override point the vendored script documents.
#
# A wrapper, not a fork: the ~200 lines upstream owns (per-SHA stamp cache,
# pytest-cov detection, the JS block, the worktree-zombie preflight) stay in one
# place and keep improving without a merge. The shipping skill's script
# resolution probes `scripts/` before the skill's own directory, so this file
# wins with no skill edit — do not delete it as a stray copy.
set -euo pipefail

PROJECT_ROOT=$(git rev-parse --show-toplevel)
cd "$PROJECT_ROOT"

# Delegate through the symlink, not skills-vendor/… — the symlink is the stable
# interface (docs/SKILLS.md § layout).
DELEGATE="skills/shipping-work-python-fastapi/scripts/pre-ship.sh"
if [[ ! -f "$DELEGATE" ]]; then
  # Worktrees don't populate submodules and AGENTS.md mandates worktree-based
  # feature work (#87), so this is a routine state, not an exotic one. Without
  # the guard it surfaces as bash's bare "No such file or directory".
  echo "ERROR: vendored gate missing at $DELEGATE" >&2
  echo "       fix: git submodule update --init --recursive   (or: bash .skills/doctor.sh)" >&2
  exit 2 # matches the delegate's own tooling/infra code — one exit-code table
fi

# The repo-wide idiom (AGENTS.md), deliberately reproduced rather than improved
# on: the gate must run under the same environment the documented workflow
# produces. `set -f` because the expansion is unquoted — a glob character in a
# secret would otherwise expand against the cwd. Parse, never source.
set -f
export $(cat /etc/usa-wa/.env "$PROJECT_ROOT/.env" 2>/dev/null | xargs)
set +f

# exec: the delegate's exit code is what the shipping skill's Iron Law reads.
exec bash "$DELEGATE" "$@"
