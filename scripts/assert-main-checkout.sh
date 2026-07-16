#!/usr/bin/env bash
# Refuse to start a prod unit unless the repo checkout is on main (issue #87).
#
# The #84 root cause: /home/exedev/usa-wa was left checked out on a feature
# branch, and the 06:30 PDC timer ran that unmerged code — minting duplicate
# anchors — because it runs `uv run --frozen --no-sync` from whatever is checked
# out. "Code committed to main is the deployed code" was convention, not
# enforcement. This is the enforcement: wired as the first ExecStartPre= on every
# code-running prod .service, so an off-main checkout fails the unit start (loud
# in the journal, and — for the OnFailure=-wired oneshots — an operator email)
# instead of silently deploying a feature branch.
#
# Detached HEAD also fails (it is likewise "not the deployed main").
# USA_WA_DEPLOY_BRANCH overrides the expected branch for a non-standard host.
set -uo pipefail

REPO=/home/exedev/usa-wa
EXPECTED="${USA_WA_DEPLOY_BRANCH:-main}"

if ! branch=$(git -C "$REPO" symbolic-ref --short HEAD 2>/dev/null); then
    echo "assert-main: refusing to start — HEAD is detached (expected '$EXPECTED')" >&2
    exit 1
fi

if [ "$branch" != "$EXPECTED" ]; then
    echo "assert-main: refusing to start — checkout on '$branch', expected '$EXPECTED'" >&2
    exit 1
fi
