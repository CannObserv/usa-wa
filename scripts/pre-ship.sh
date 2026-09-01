#!/usr/bin/env bash
# Project-local pre-ship gate (#172).
#
# Loads usa-wa's two env files (AGENTS.md § Environment Variables), then hands
# off to the vendored gate. Needed because the gate runs the *full* suite, whose
# db-marked majority needs TEST_DATABASE_URL — on a clean shell the test phase
# died wholesale, and a gate that fails for non-code reasons trains people to
# wave it through. (Before #185 it died even harder: conftest.py raised at
# import, so the phase ended in a bare ImportError before collecting anything.)
# This is the override point the vendored script documents.
#
# #296: "the two env files" is one file short in a worktree — `.env` is
# git-ignored, so the checkout AGENTS.md mandates feature work happen in (#87)
# is the one that never has it. The loader below falls back to the primary
# checkout's copy, and refuses outright rather than exec'ing the gate with a
# half-loaded environment.
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

# Production secrets. A variable, not a literal, so the suite can point the
# probe somewhere it owns — /etc/usa-wa/.env is a host file no test can predict,
# and one test below asserts what happens when NO file carries
# TEST_DATABASE_URL. Prod never sets this; the default is the documented path.
SYSTEM_ENV="${PRE_SHIP_SYSTEM_ENV:-/etc/usa-wa/.env}"

# Dev/agent secrets, TEST_DATABASE_URL among them (AGENTS.md § Environment
# Variables). `.env` is git-ignored, so `git worktree add` never produces one and
# nothing in worktree-create.sh seeds it — and AGENTS.md § Server Lifecycle
# *mandates* worktree feature work (#87). So the checkout the repo tells you to
# work in was the one checkout where this file is reliably absent (#296).
#
# The old one-liner swallowed that with `2>/dev/null`, exec'd the gate with a
# half-loaded environment, and died ~60 s later inside conftest_db.py printing a
# remediation that named the same absent file. Falling back to the primary
# checkout's copy is the resolution socraticode-health.sh already uses for the
# same reason: --git-common-dir is the shared .git for both a worktree and the
# primary checkout, and its parent is the primary checkout.
#
# --path-format=absolute needs git >= 2.31; without it --git-common-dir is
# relative to cwd in the primary checkout (plain `.git`) and absolute in a
# worktree, so the fallback resolves it against PROJECT_ROOT.
REPO_ENV="$PROJECT_ROOT/.env"
FALLBACK_NOTE=""
# Built once, here, so the refusal below reports exactly the files that were
# consulted — one line each. Assembling it at print time listed
# $PROJECT_ROOT/.env twice whenever no fallback was taken, which is the common
# case, in the one message whose entire job is precision.
CONSULTED=("$SYSTEM_ENV" "$REPO_ENV")
if [[ ! -f "$REPO_ENV" ]]; then
  commondir=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || commondir=""
  if [[ -z "$commondir" ]]; then
    commondir=$(git rev-parse --git-common-dir 2>/dev/null) || commondir=""
    case "$commondir" in ""|/*) ;; *) commondir="$PROJECT_ROOT/$commondir" ;; esac
  fi
  if [[ -n "$commondir" ]]; then
    MAIN_ROOT=$(dirname "$commondir")
    # `!= $PROJECT_ROOT` is not redundant: in the primary checkout MAIN_ROOT *is*
    # PROJECT_ROOT, and the -f test above already said there is no .env there.
    # Without the guard a layout where dirname(commondir) is not a checkout at
    # all (a bare repo's worktree) would still be probed, which is harmless, but
    # the note below would claim a fallback that changed nothing.
    if [[ "$MAIN_ROOT" != "$PROJECT_ROOT" && -f "$MAIN_ROOT/.env" ]]; then
      REPO_ENV="$MAIN_ROOT/.env"
      CONSULTED+=("$REPO_ENV")
      FALLBACK_NOTE="pre-ship: no .env in this checkout (worktrees never inherit one); loaded $REPO_ENV"
    fi
  fi
fi

# The repo-wide idiom (AGENTS.md), deliberately reproduced rather than improved
# on: the gate must run under the same environment the documented workflow
# produces. `set -f` because the expansion is unquoted — a glob character in a
# secret would otherwise expand against the cwd. Parse, never source.
#
# Existing files only, and `cat` unredirected: the missing-file error is now the
# `2>/dev/null` that hid #296, so nothing here may be silenced.
ENV_FILES=()
[[ -f "$SYSTEM_ENV" ]] && ENV_FILES+=("$SYSTEM_ENV")
[[ -f "$REPO_ENV" ]] && ENV_FILES+=("$REPO_ENV")
if [[ ${#ENV_FILES[@]} -gt 0 ]]; then
  set -f
  export $(cat "${ENV_FILES[@]}" | xargs)
  set +f
fi

# Announced, not silent. A gate whose environment depends on an invisible probe
# is how #296 stayed invisible for the ~60 s it took the test phase to die.
[[ -n "$FALLBACK_NOTE" ]] && echo "$FALLBACK_NOTE" >&2

# Refuse here rather than ~60 s downstream. The gate runs the FULL suite, whose
# db-marked majority needs this variable, so an unset one is a gate that cannot
# run — and the failure it produces on its own names files by a fixed recipe
# rather than by what this checkout actually has.
if [[ -z "${TEST_DATABASE_URL:-}" ]]; then
  echo "ERROR: TEST_DATABASE_URL is unset after loading every env file this gate knows about," >&2
  echo "       so the db-marked majority of the suite cannot run. Consulted:" >&2
  for candidate in "${CONSULTED[@]}"; do
    if [[ -f "$candidate" ]]; then
      echo "         $candidate (read; no TEST_DATABASE_URL)" >&2
    else
      echo "         $candidate (absent)" >&2
    fi
  done
  echo "       fix: put TEST_DATABASE_URL in $PROJECT_ROOT/.env — it is git-ignored," >&2
  echo "            so it will not be committed (AGENTS.md § Environment Variables)." >&2
  exit 2 # tooling/infra, like the missing-delegate guard above — one exit-code table
fi

# exec: the delegate's exit code is what the shipping skill's Iron Law reads.
exec bash "$DELEGATE" "$@"
