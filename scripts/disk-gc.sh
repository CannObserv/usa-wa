#!/usr/bin/env bash
# Host disk garbage collector + sensor (issue #394).
#
# #394 was filed after `pytest` died with ENOSPC mid-session: the root volume hit
# 98% with 565 MB free, and the growth was almost entirely OUTSIDE the repo —
# tooling that keeps every version it has ever installed. Five VS Code server
# builds (2 live), five copies of SocratiCode's node tree (2 live), an ollama
# image carrying 4 GB of GPU libraries for a host with no GPU.
#
# Three rules this script is built on:
#
#   1. PRUNE ONLY WHAT IS PROVABLY UNREFERENCED. The reclaimable copy and the
#      in-use one are siblings in one directory and look identical to `ls`. Every
#      candidate is therefore checked against the live process table before
#      removal. A size-or-mtime heuristic would delete a running editor's server.
#
#   2. NEVER TOUCH REPO DATA. Retention for the published-dataset, raw/, dbt and
#      cassette tiers is a *contract* question, descoped to #396. This script
#      measures those tiers and removes nothing from them.
#
#   3. RUN WHEN THE REPO IS BROKEN. Plain bash, no virtualenv, no `uv`. The unit
#      is deliberately exempt from the #87 branch guard and the #279 venv guard,
#      because disk pressure is *more* likely while a worktree is checked out or
#      a venv is half-synced — exactly when a guarded unit would refuse to start.
#
# Usage:
#   disk-gc.sh              report only; removes nothing
#   disk-gc.sh --prune      reclaim, then report
#   disk-gc.sh --json       machine-readable single-object output
#
# Exit: 0 healthy or warning, 1 free space below the fail threshold, 2 tooling.
#
# Pinned by scripts/tests/test_disk_gc.py.
set -uo pipefail

PRUNE=0
JSON=0
for arg in "$@"; do
    case "$arg" in
        --prune) PRUNE=1 ;;
        --json) JSON=1 ;;
        -h | --help)
            # Derived, not hardcoded: the header runs to the first line that
            # is not a comment, so inserting one cannot silently truncate the
            # usage text or leak the `Pinned by` line into it.
            sed -n '2,/^[^#]/p' "$0" | sed '/^# Pinned by/,$d' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "disk-gc: unknown argument: $arg" >&2
            exit 2
            ;;
    esac
done

MOUNT=${DISK_GC_MOUNT:-/}
VSCODE_ROOT=${DISK_GC_VSCODE_ROOT:-$HOME/.vscode-server}
EXTENSIONS_ROOT=${DISK_GC_EXTENSIONS_ROOT:-$VSCODE_ROOT/extensions}
PLUGIN_ROOT=${DISK_GC_PLUGIN_ROOT:-$HOME/.claude/plugins}
NPX_ROOT=${DISK_GC_NPX_ROOT:-$HOME/.npm/_npx}
REPO=${DISK_GC_REPO:-/home/exedev/usa-wa}
DOCKER=${DISK_GC_DOCKER-docker}
# 2 GiB / 1 GiB. The measured volatility behind these: 1.3 G → 565 M in 28 h of
# ordinary session work, so a threshold that leaves less than one worktree's
# venv (~225 MB) of headroom is a threshold that fires after the damage.
WARN_BYTES=${DISK_GC_WARN_BYTES:-2147483648}
FAIL_BYTES=${DISK_GC_FAIL_BYTES:-1073741824}
# Minutes a candidate must have been untouched before it may be removed.
# Liveness cannot answer this one: a tree being installed RIGHT NOW is named by
# no running process, because the process that will run out of it does not exist
# yet. #389 established that a ~457 MB `_npx` install happens at session start,
# and this timer fires daily at 05:45 — so without a grace window a session
# starting a minute earlier gets its half-written install deleted under it, and
# nothing detects or repairs the partial tree afterwards.
GRACE_MINUTES=${DISK_GC_GRACE_MINUTES:-60}

# Every numeric input, validated before anything is read or removed. Left
# unchecked these all failed OPEN: `DISK_GC_GRACE_MINUTES=abc` made bash print
# `[: abc: integer expression expected` and then SKIP the grace window, so a
# typo in the unit's Environment= silently disarmed the guard that stops
# --prune deleting a half-written install — signalled only by one stderr line
# in the journal. A misconfigured garbage collector must refuse to run, not run
# with its safeties off.
for setting in WARN_BYTES FAIL_BYTES GRACE_MINUTES; do
    case "${!setting}" in
        '' | *[!0-9]*)
            echo "disk-gc: DISK_GC_$setting must be a non-negative integer, got '${!setting}'" >&2
            exit 2
            ;;
    esac
done

# `find` is how the grace window is enforced; without it recently_touched would
# return "not recent" for everything and the window would silently not exist.
command -v find >/dev/null 2>&1 || {
    echo "disk-gc: find not found — the grace window cannot be enforced" >&2
    exit 2
}
#: The label scripts/slim-ollama-image.sh stamps on the rebuilt image.
OLLAMA_SLIM_LABEL="cpu-only-gpu-libs-stripped"

# ── liveness ──────────────────────────────────────────────────────────────────
#
# Snapshotted ONCE, before anything else runs. Order is load-bearing: `du` on a
# candidate puts that candidate's path into a child process's command line, so a
# process table read afterwards would report every candidate as live and the GC
# would silently never reclaim anything. Self and parent are excluded for the
# same reason.
#
# Four sources, not one. A command line is the usual evidence, but it is not
# the only way a process depends on a tree: one started by a relative path after
# a chdir, or through a symlinked entry point, names the directory nowhere in
# its argv while still running out of it. `cwd` and `exe` close that, and the
# whole point of the liveness check is that a false negative means `rm -rf` on
# something in use.
#
# `maps` is the fourth (#399 CR 1): the file-backed memory mappings, i.e. every
# shared library and native addon a process has loaded. An un-reloaded VS Code
# window's extension host still runs an old Claude extension version and names
# its directory in none of the other three — the mapped `audio-capture.node` is
# its only trace. Deduplicated as it is read, since every process maps libc.
#
# Held in memory, never in a file (#399 CR 5). This script runs because the disk
# is full, and a snapshot written to that disk truncated silently at ENOSPC:
# every process the truncation lost then read as idle, which turned --prune into
# `rm -rf` on trees in use. `awk` dedupes without ever spilling to a temp file
# the way `sort` can. A failed or empty snapshot is a refusal, never an empty
# process table. The trailing `:` keeps an unreadable last pid's status from
# reading as the snapshot failing.
LIVE=$(
    for entry in /proc/[0-9]*; do
        pid=${entry#/proc/}
        [ "$pid" = "$$" ] && continue
        [ "$pid" = "$PPID" ] && continue
        tr '\0' '\n' <"$entry/cmdline" 2>/dev/null
        readlink "$entry/cwd" 2>/dev/null
        readlink "$entry/exe" 2>/dev/null
        grep -o '/.*' "$entry/maps" 2>/dev/null
        :
    done | awk '!seen[$0]++'
) && [ -n "$LIVE" ] || {
    echo "disk-gc: could not snapshot the process table — refusing to judge liveness" >&2
    exit 2
}

is_live() {
    # A substring match on the string itself, not `grep -q`: under pipefail a
    # pipe into an early-exiting grep can report failure on SIGPIPE, and a
    # here-string can spill to a temp file — either way a live tree reads idle.
    [[ $LIVE == *"$1"* ]]
}

names() {
    # names <list> <line> — does the newline-separated <list> hold <line>
    # exactly? A match on the string, for is_live's reason (#400 CR 2): on a
    # list longer than the pipe buffer, `printf | grep -qxF` matching near the
    # top SIGPIPEs the printf, and the match reads as a miss — an installed
    # version turned candidate.
    [[ $'\n'$1$'\n' == *$'\n'"$2"$'\n'* ]]
}

size_of() {
    # Always exactly one integer on stdout. `du` prints a total AND exits
    # non-zero when it could not descend everywhere (an unreadable subdirectory,
    # or a file that vanished mid-scan), so `du … || echo 0` emits BOTH the
    # total and the fallback — two lines, which then break `$((… + bytes))` with
    # a syntax error and emit `"bytes":3\n0` into the JSON. Capture first,
    # validate, then decide.
    local total
    [ -e "$1" ] || {
        echo 0
        return
    }
    total=$(du -sb -- "$1" 2>/dev/null | head -n1 | cut -f1)
    case "$total" in
        '' | *[!0-9]*) echo 0 ;;
        *) echo "$total" ;;
    esac
}

# ── candidates ────────────────────────────────────────────────────────────────

CAND_KINDS=()
CAND_PATHS=()
CAND_BYTES=()
WARNINGS=()
#: Candidates the grace window held back. Reported separately because
#: "reclaimed: 0B in 0 item(s)" was byte-identical whether the window had
#: withheld half a gigabyte or there was genuinely nothing to do — true either
#: way, and misleading in exactly the tool someone opens when the disk is
#: filling. Sizes are deliberately not measured: walking a tree that is being
#: written to is what the window exists to avoid.
WITHHELD_KINDS=()
WITHHELD_PATHS=()

recently_touched() {
    # Anything under the tree modified inside the grace window, not just the
    # directory itself: an installer writing files deep inside leaves the top
    # level's own mtime untouched.
    [ "$GRACE_MINUTES" -le 0 ] && return 1
    [ -n "$(find "$1" -mmin "-$GRACE_MINUTES" -print -quit 2>/dev/null)" ]
}

consider() {
    # consider <kind> <path> — record it unless some running process names it,
    # or it is still being written.
    local kind=$1 path=$2
    [ -e "$path" ] || return 0
    if is_live "$path"; then
        return 0
    fi
    if recently_touched "$path"; then
        WITHHELD_KINDS+=("$kind")
        WITHHELD_PATHS+=("$path")
        return 0
    fi
    CAND_KINDS+=("$kind")
    CAND_PATHS+=("$path")
    CAND_BYTES+=("$(size_of "$path")")
}

# VS Code keeps every server build it has ever downloaded, and the CLI binary
# that goes with each. `lru.json` sits beside them and is not a build.
for build in "$VSCODE_ROOT"/cli/servers/Stable-*; do
    [ -d "$build" ] && consider vscode-server "$build"
done
for cli in "$VSCODE_ROOT"/code-*; do
    [ -e "$cli" ] && consider vscode-cli "$cli"
done

# Claude plugin cache: one full node tree per version ever installed. The
# manifest is the only evidence of which version is current, so an unreadable
# manifest means "remove nothing" rather than "remove everything" — and a
# manifest that parses but names nothing installed is the same absence of
# evidence, not a licence to clear the cache. Each tree is ~646 MB, and a
# partially written manifest is a likelier explanation than a real empty
# install. A manifest naming only versions that are not on disk is the same
# absence (#400) — a half-finished install, or one written ahead of extraction —
# and pruning the rest would leave the plugin nothing to run. Evidence is
# therefore the manifest naming at least one version actually present. Lacking
# it is said, not swallowed: a silent refusal is the `reclaimable: 0B` #399 was
# filed for.
#
# The one gap left: evidence is judged across the cache, not per plugin. With
# two plugins installed, one whose entry names a present version licenses
# pruning the other's even if that other's entry names nothing on disk.
#
# `cache/<marketplace>/<plugin>/<version>` — the layout every installed plugin
# uses here. A deeper or shallower one would not be matched.
PLUGIN_VERSIONS=()
for version_dir in "$PLUGIN_ROOT"/cache/*/*/*; do
    [ -d "$version_dir" ] && PLUGIN_VERSIONS+=("$version_dir")
done
if [ "${#PLUGIN_VERSIONS[@]}" -gt 0 ]; then
    MANIFEST="$PLUGIN_ROOT/installed_plugins.json"
    # Any error — missing file, bad JSON, an entry of the wrong shape, no
    # python3 — empties the list rather than leaving a partial one.
    INSTALLED=$(python3 -c '
import json, sys
with open(sys.argv[1]) as fh:
    doc = json.load(fh)
for entries in doc.get("plugins", {}).values():
    for entry in entries:
        path = entry.get("installPath")
        if path:
            print(path)
' "$MANIFEST" 2>/dev/null) || INSTALLED=
    plugin_evidence=0
    for version_dir in "${PLUGIN_VERSIONS[@]}"; do
        names "$INSTALLED" "$version_dir" && plugin_evidence=1
    done
    if [ "$plugin_evidence" -eq 1 ]; then
        for version_dir in "${PLUGIN_VERSIONS[@]}"; do
            names "$INSTALLED" "$version_dir" && continue
            consider plugin-cache "$version_dir"
        done
    else
        WARNINGS+=("$MANIFEST is absent, unparseable, or names no plugin version on disk — ${#PLUGIN_VERSIONS[@]} version(s) left unevaluated, none removed")
    fi
fi

# VS Code extensions (#399). The Claude Code extension ships a ~220 MB native
# binary per version and keeps every one it has installed — about one a week.
# Two states protect a version, and a version can be either without the other:
# LIVE (a running agent `exe`s its bundled binary, or a window's extension host
# has its native addon mapped — `consider` checks both) and ACTIVE (named by
# extensions.json, the manifest the editor starts the next agent from). The
# active one is kept even with nothing live: an editor between reloads runs no
# agent, and that is no licence to delete what it will start next.
#
# The one gap left: the addon is loaded lazily, so an un-reloaded window whose
# extension host never touched it, with no agent running, names its old version
# nowhere. Pruning that costs the window a reload — which VS Code already asks
# for after an update — not data.
#
# Same evidence rule as the plugin cache above: an absent or unparseable
# manifest, one naming no Claude extension, and one naming a version that is not
# on disk are all absence of evidence — remove nothing, and say so.
#
# Scoped to the Claude extension. Other publishers' extensions are small, and
# "the manifest omits it" is weaker evidence than a match on the one id this
# tier is about; `[0-9]` anchors the version so a sibling id like
# `anthropic.claude-code-foo` is not read as one. `.obsolete`, VS Code's own
# removal list, is not evidence either: when #399 was measured it listed 2.1.273
# while a live agent was running out of it. Matched by directory name —
# `relativeLocation`, or the basename of `location` in manifests that predate
# it — so a relocated or symlinked root still matches.
#
# Deliberately NOT roots: ~/.local/share/claude/versions/ (the native installer's
# tier, the same keep-everything shape) and ~/.local/share/claude-rollback/
# (#398's only copy of the pre-update binary — a rollback artefact, not a cache;
# it must never be auto-pruned).
EXT_VERSIONS=()
for version_dir in "$EXTENSIONS_ROOT"/anthropic.claude-code-[0-9]*; do
    [ -d "$version_dir" ] && EXT_VERSIONS+=("$version_dir")
done
if [ "${#EXT_VERSIONS[@]}" -gt 0 ]; then
    EXT_MANIFEST="$EXTENSIONS_ROOT/extensions.json"
    # Any error — missing file, bad JSON, an entry of the wrong shape, no
    # python3 — empties the list rather than leaving a partial one.
    ACTIVE_EXT=$(python3 -c '
import json, os, sys
with open(sys.argv[1]) as fh:
    doc = json.load(fh)
for entry in doc:
    if entry["identifier"]["id"].lower() != "anthropic.claude-code":
        continue
    location = entry.get("location") or {}
    name = entry.get("relativeLocation") or os.path.basename(
        location.get("fsPath") or location.get("path") or ""
    )
    if name:
        print(name)
' "$EXT_MANIFEST" 2>/dev/null) || ACTIVE_EXT=
    ext_evidence=0
    for version_dir in "${EXT_VERSIONS[@]}"; do
        names "$ACTIVE_EXT" "${version_dir##*/}" && ext_evidence=1
    done
    if [ "$ext_evidence" -eq 1 ]; then
        for version_dir in "${EXT_VERSIONS[@]}"; do
            names "$ACTIVE_EXT" "${version_dir##*/}" && continue
            consider vscode-extension "$version_dir"
        done
    else
        WARNINGS+=("$EXT_MANIFEST is absent, unparseable, or names no Claude extension version on disk — ${#EXT_VERSIONS[@]} version(s) left unevaluated, none removed")
    fi
fi

# npx caches. #389 pinned the SocratiCode server but named the limitation that
# makes this recur: Claude Code cannot override a plugin's MCP command, so the
# plugin keeps launching @latest and minting a fresh ~457 MB tree per release.
for cache in "$NPX_ROOT"/*; do
    [ -d "$cache" ] && consider npx-cache "$cache"
done

# ── prune or account ──────────────────────────────────────────────────────────

PRUNED_KINDS=()
PRUNED_PATHS=()
PRUNED_BYTES=()
PRUNED_TOTAL=0
#: Candidates a --prune run tried and failed to remove. They stay in the
#: reclaimable accounting, so the report distinguishes "nothing to reclaim" from
#: "could not reclaim it".
UNREMOVED_KINDS=()
UNREMOVED_PATHS=()
UNREMOVED_BYTES=()
RECLAIMABLE_TOTAL=0

for i in "${!CAND_PATHS[@]}"; do
    bytes=${CAND_BYTES[$i]}
    if [ "$PRUNE" -eq 1 ]; then
        if rm -rf -- "${CAND_PATHS[$i]}"; then
            PRUNED_KINDS+=("${CAND_KINDS[$i]}")
            PRUNED_PATHS+=("${CAND_PATHS[$i]}")
            PRUNED_BYTES+=("$bytes")
            PRUNED_TOTAL=$((PRUNED_TOTAL + bytes))
        else
            # Still reclaimable — it just was not reclaimed. Counting it as
            # neither pruned nor reclaimable made a run that failed to remove
            # 2 GB report "pruned_bytes: 0, reclaimable_bytes: 0", i.e. nothing
            # to do, which is the opposite of the truth.
            WARNINGS+=("could not remove ${CAND_PATHS[$i]}")
            RECLAIMABLE_TOTAL=$((RECLAIMABLE_TOTAL + bytes))
            UNREMOVED_KINDS+=("${CAND_KINDS[$i]}")
            UNREMOVED_PATHS+=("${CAND_PATHS[$i]}")
            UNREMOVED_BYTES+=("$bytes")
        fi
    else
        RECLAIMABLE_TOTAL=$((RECLAIMABLE_TOTAL + bytes))
    fi
done

# ── repo tiers: measured, never pruned (#396 owns their retention) ────────────

TIER_NAMES=(datasets raw dbt_logs duckdb venv git worktrees)
TIER_PATHS=(
    "$REPO/data/datasets"
    "$REPO/raw"
    "$REPO/data/dbt-logs"
    "$REPO/data/pipeline.duckdb"
    "$REPO/.venv"
    "$REPO/.git"
    "$REPO/.worktrees"
)
TIER_BYTES=()
for path in "${TIER_PATHS[@]}"; do
    TIER_BYTES+=("$(size_of "$path")")
done

# Each live worktree costs its own venv (~225 MB) because
# .skills/worktree_venv=none deliberately links none (#279). Destroying one is
# gated by the using-git-worktrees Iron Law — a merge check this script cannot
# make — so it is named, not removed.
for worktree in "$REPO"/.worktrees/*; do
    [ -d "$worktree" ] || continue
    WARNINGS+=("worktree present: $(basename "$worktree") ($(size_of "$worktree") bytes) — destroy with the using-git-worktrees skill once merged")
done

# The ollama image carries ~4 GB of CUDA/ROCm libraries this host has no device
# for. scripts/slim-ollama-image.sh strips them and stamps a label; a plain
# `docker pull ollama/ollama:latest` silently restores the fat image, and
# nothing else on the box would report that.
if [ -n "$DOCKER" ] && command -v "$DOCKER" >/dev/null 2>&1; then
    if label=$("$DOCKER" image inspect ollama/ollama:latest \
        --format '{{index .Config.Labels "dev.usa-wa.slim"}}' 2>/dev/null); then
        if [ "$label" != "$OLLAMA_SLIM_LABEL" ]; then
            WARNINGS+=("ollama/ollama:latest is not the slim build — a pull restored the GPU libraries (~7.4 GB); re-run scripts/slim-ollama-image.sh")
        fi
    fi
fi

# ── the sensor reading ────────────────────────────────────────────────────────

read -r TOTAL_KB FREE_KB <<<"$(df -Pk "$MOUNT" | awk 'NR==2 {print $2, $4}')"
# Zero total, not just empty: a pseudo-filesystem or an odd container mount
# reports 0, which would divide by zero in the used-percentage below.
if [ -z "${TOTAL_KB:-}" ] || [ -z "${FREE_KB:-}" ] || [ "$TOTAL_KB" -eq 0 ]; then
    echo "disk-gc: could not read free space for $MOUNT" >&2
    exit 2
fi
TOTAL_BYTES=$((TOTAL_KB * 1024))
FREE_BYTES=$((FREE_KB * 1024))
USED_PCT=$(((TOTAL_BYTES - FREE_BYTES) * 100 / TOTAL_BYTES))

STATUS=ok
RC=0
if [ "$FREE_BYTES" -lt "$FAIL_BYTES" ]; then
    STATUS=fail
    RC=1
elif [ "$FREE_BYTES" -lt "$WARN_BYTES" ]; then
    STATUS=warn
fi

# ── output ────────────────────────────────────────────────────────────────────

json_string() {
    printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' 2>/dev/null ||
        printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"
}

emit_entries() {
    # emit_entries <kinds-array-name> <paths-array-name> <bytes-array-name>
    local -n kinds=$1 paths=$2 bytes=$3
    local first=1 i
    for i in "${!paths[@]}"; do
        [ "$first" -eq 1 ] || printf ','
        first=0
        printf '{"kind":%s,"path":%s,"bytes":%s}' \
            "$(json_string "${kinds[$i]}")" "$(json_string "${paths[$i]}")" "${bytes[$i]}"
    done
}

if [ "$JSON" -eq 1 ]; then
    printf '{'
    printf '"mount":%s,' "$(json_string "$MOUNT")"
    printf '"total_bytes":%s,"free_bytes":%s,"used_pct":%s,' "$TOTAL_BYTES" "$FREE_BYTES" "$USED_PCT"
    printf '"status":%s,' "$(json_string "$STATUS")"
    printf '"pruned_bytes":%s,' "$PRUNED_TOTAL"
    printf '"pruned":['
    emit_entries PRUNED_KINDS PRUNED_PATHS PRUNED_BYTES
    printf '],'
    printf '"reclaimable_bytes":%s,' "$RECLAIMABLE_TOTAL"
    printf '"reclaimable":['
    if [ "$PRUNE" -eq 1 ]; then
        emit_entries UNREMOVED_KINDS UNREMOVED_PATHS UNREMOVED_BYTES
    else
        emit_entries CAND_KINDS CAND_PATHS CAND_BYTES
    fi
    printf '],'
    printf '"withheld":['
    for i in "${!WITHHELD_PATHS[@]}"; do
        [ "$i" -eq 0 ] || printf ','
        printf '{"kind":%s,"path":%s}' \
            "$(json_string "${WITHHELD_KINDS[$i]}")" "$(json_string "${WITHHELD_PATHS[$i]}")"
    done
    printf '],'
    printf '"grace_minutes":%s,' "$GRACE_MINUTES"
    printf '"tiers":{'
    for i in "${!TIER_NAMES[@]}"; do
        [ "$i" -eq 0 ] || printf ','
        printf '%s:%s' "$(json_string "${TIER_NAMES[$i]}")" "${TIER_BYTES[$i]}"
    done
    printf '},'
    printf '"warnings":['
    for i in "${!WARNINGS[@]}"; do
        [ "$i" -eq 0 ] || printf ','
        json_string "${WARNINGS[$i]}"
    done
    printf ']'
    printf '}\n'
else
    human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1"; }
    echo "disk-gc: $MOUNT — $(human "$FREE_BYTES") free of $(human "$TOTAL_BYTES") (${USED_PCT}% used) [$STATUS]"
    if [ "$PRUNE" -eq 1 ]; then
        echo "reclaimed: $(human "$PRUNED_TOTAL") in ${#PRUNED_PATHS[@]} item(s)"
        for i in "${!PRUNED_PATHS[@]}"; do
            printf '  - %-16s %10s  %s\n' "${PRUNED_KINDS[$i]}" "$(human "${PRUNED_BYTES[$i]}")" "${PRUNED_PATHS[$i]}"
        done
        if [ "${#UNREMOVED_PATHS[@]}" -gt 0 ]; then
            echo "NOT reclaimed: $(human "$RECLAIMABLE_TOTAL") in ${#UNREMOVED_PATHS[@]} item(s) — removal failed"
            for i in "${!UNREMOVED_PATHS[@]}"; do
                printf '  ! %-16s %10s  %s\n' "${UNREMOVED_KINDS[$i]}" "$(human "${UNREMOVED_BYTES[$i]}")" "${UNREMOVED_PATHS[$i]}"
            done
        fi
    else
        echo "reclaimable: $(human "$RECLAIMABLE_TOTAL") in ${#CAND_PATHS[@]} item(s) — re-run with --prune"
        for i in "${!CAND_PATHS[@]}"; do
            printf '  - %-16s %10s  %s\n' "${CAND_KINDS[$i]}" "$(human "${CAND_BYTES[$i]}")" "${CAND_PATHS[$i]}"
        done
    fi
    if [ "${#WITHHELD_PATHS[@]}" -gt 0 ]; then
        echo "withheld: ${#WITHHELD_PATHS[@]} item(s) modified within the last ${GRACE_MINUTES}m — still being written, or recently used"
        for i in "${!WITHHELD_PATHS[@]}"; do
            printf '  ~ %-16s %10s  %s\n' "${WITHHELD_KINDS[$i]}" "-" "${WITHHELD_PATHS[$i]}"
        done
    fi
    echo "repo tiers (measured, never pruned here — #396 owns their retention):"
    for i in "${!TIER_NAMES[@]}"; do
        printf '  - %-10s %10s  %s\n' "${TIER_NAMES[$i]}" "$(human "${TIER_BYTES[$i]}")" "${TIER_PATHS[$i]}"
    done
    for warning in ${WARNINGS+"${WARNINGS[@]}"}; do
        echo "warning: $warning" >&2
    done
fi

exit "$RC"
