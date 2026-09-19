#!/usr/bin/env bash
# Refuse to start a prod unit unless the venv's editable installs point into the
# production checkout (issue #279).
#
# The #279 root cause: /home/exedev/usa-wa is both usa-wa.service's
# WorkingDirectory= and the parent of every worktree, and the worktree skill's
# default symlinked the prod .venv into each new one. `uv run` reinstalls the
# workspace project, so a single `uv run pytest` inside a worktree restamped every
# editable install in the SHARED production venv at that worktree's paths:
#
#   - usa-wa-common==0.1.0 (from file:///home/exedev/usa-wa/.worktrees/…/packages/usa-wa-common)
#   + usa-wa-common==0.1.0 (from file:///home/exedev/usa-wa/packages/usa-wa-common)
#
# `.skills/worktree_venv=none` closes that door (docs/SKILLS.md § Worktree venv
# isolation). This is the detector for a venv already in that state — by a linked
# worktree predating the knob, a hand-run `uv run --project`, or any future tool
# whose *run* verb reinstalls the project.
#
# WHY A GUARD AT ALL, given the knob. The corruption is LATENT: it happens at
# worktree-test time and detonates at the next unit start, potentially days later
# and run by someone with no idea a worktree was involved. And it detonates naming
# the wrong thing —
#
#   ModuleNotFoundError: No module named 'clearinghouse_core'
#   ValueError: Cannot resolve 'clearinghouse_core.logging.build_json_formatter'
#   ValueError: Unable to configure formatter 'json'
#
# — which reads as a logging-config defect. Cheap to detect, expensive to
# diagnose. Neither existing guard covers it: `--frozen --no-sync` (#30) stops a
# unit start from mutating the venv and says nothing about a worktree mutating it
# out of band, and assert-main-checkout.sh (#87) guards the checked-out branch,
# not the tree the venv resolves imports from. Both are one-directional; this
# comes from the other side.
#
# WHAT IS CHECKED: each `*.dist-info/direct_url.json` — the PEP 610 record of
# where an install came from. That is precisely what `uv sync --locked` reported
# and repaired in #279, and it is rewritten in lockstep with the `.pth` file that
# actually places the tree on sys.path. The `.pth` files are deliberately NOT
# scanned: their naming is a build-backend detail, and distinguishing a
# third-party `.pth` that legitimately names a path elsewhere from a corrupted one
# would trade a real diagnosis for a guard that can wedge production on a false
# positive.
#
# USA_WA_DEPLOY_ROOT overrides the checkout root — for a non-standard host, and
# for scripts/tests/test_assert_venv_integrity.py, which drives every branch below
# through fabricated venvs rather than the live one.
set -uo pipefail
shopt -s nullglob

ROOT="${USA_WA_DEPLOY_ROOT:-/home/exedev/usa-wa}"
VENV="$ROOT/.venv"
REPAIR="cd $ROOT && uv sync --locked"

if [ ! -d "$VENV" ]; then
    echo "assert-venv: refusing to start — no .venv at $VENV; repair with: $REPAIR" >&2
    exit 1
fi

editable=0
outside=0

# lib/python*/ is a glob on purpose: a Python upgrade moves the whole directory,
# and a hardcoded 3.12 would then scan nothing and pass — silently, on the one day
# the venv is most likely to have been rebuilt wrong.
for record in "$VENV"/lib/python*/site-packages/*.dist-info/direct_url.json; do
    payload=$(tr -d '[:space:]' <"$record") || continue

    # Whitespace is stripped so that the field probes below hold for any
    # serializer: PEP 610 fixes the field NAMES, not the spacing, and keying a
    # parser on an exact upstream string is what AGENTS.md § Project Layout
    # forbids. The one thing this cannot survive is a path containing whitespace,
    # which would be squashed — and then reads as not-a-workspace-member, i.e.
    # fails closed. No path in this deployment has one.
    case "$payload" in
    *'"editable":true'*) ;;
    # A non-editable record — a wheel built from a local path — is COPIED into the
    # venv, so where it was built says nothing about where imports resolve. Only
    # editable installs carry the worktree hazard.
    *) continue ;;
    esac
    editable=$((editable + 1))

    # Only the `file:///…` triple-slash form uv writes is unwrapped. `file:/path`
    # (also valid per RFC 8089) and percent-encoded paths fall through with the
    # scheme still attached and are reported — fail-closed, and deliberate: a
    # URL-decoder here would be more code able to get a production start wrong
    # than the one shape this deployment produces is worth. (CR 5)
    url=${payload#*'"url":"'}
    url=${url%%'"'*}
    path=${url#file://}

    # usa_wa_common-0.1.0.dist-info → usa_wa_common. dist-info normalises `-` to
    # `_` inside the name, so the last `-` is always the version separator.
    package=${record%/direct_url.json}
    package=${package##*/}
    package=${package%.dist-info}
    package=${package%-*}

    # An ALLOWLIST, not a prefix test against $ROOT — and that is the whole
    # subtlety of #279. A worktree lives INSIDE the checkout
    # (/home/exedev/usa-wa/.worktrees/<slug>/packages/…), so "is it under $ROOT?"
    # answers yes for precisely the corrupted state this guard exists to catch.
    # What `uv sync --locked` restores is one exact shape — $ROOT/packages/<name>,
    # a single segment — so assert that instead. Anything deeper is a worktree;
    # anything else is another tree entirely, including a sibling whose name
    # merely starts with the root's (/home/exedev/usa-wa-scratch).
    #
    # It is coupled to the AGENTS.md § Project Layout `packages/*` workspace, on
    # purpose: if that moves, this fails loudly on the next unit start rather than
    # quietly approving whatever replaced it.
    member=${path#"$ROOT/packages/"}
    if [ "$member" != "$path" ] && [ -n "$member" ] && [ "$member" = "${member%%/*}" ]; then
        continue
    fi

    echo "assert-venv: $package is installed editable from $path" \
        "— not a workspace member under $ROOT/packages/" >&2
    outside=$((outside + 1))
done

if [ "$outside" -gt 0 ]; then
    echo "assert-venv: refusing to start — $outside editable install(s) resolve outside" \
        "$ROOT/packages/, so this unit would import a tree that may no longer exist" \
        "(#279). Repair with: $REPAIR" >&2
    exit 1
fi

if [ "$editable" -eq 0 ]; then
    # The other way this venv is unusable, and the classic way a check of this
    # shape passes for the wrong reason: nothing to scan, so nothing found wrong.
    # A unit starting against it raises the same ModuleNotFoundError.
    echo "assert-venv: refusing to start — no editable workspace installs found under $VENV;" \
        "repair with: $REPAIR" >&2
    exit 1
fi

exit 0
