#!/usr/bin/env bash
# Static validation gate for systemd units under deploy/ (issue #51).
#
# Wraps `systemd-analyze verify`, which alone is too lax for hand-edited
# templates: unknown/misspelled directives and bad [Section] names warn on
# stderr but exit 0. So we fail on warning markers as well as a non-zero exit.
#
# What this catches: directive/section typos, malformed syntax, nonexistent
# ExecStart binaries. What it does NOT catch: misspelled After=/Before=
# ordering deps (systemd treats ordering against absent units as legitimate) —
# those need a separate edge-assertion test.
#
# Usage: verify-units.sh <unit-file>...  (pre-commit passes the changed paths)
set -uo pipefail

if ! command -v systemd-analyze >/dev/null 2>&1; then
    echo "verify-units: systemd-analyze not installed; skipping" >&2
    exit 0
fi

if [ "$#" -eq 0 ]; then
    exit 0
fi

# stderr lines that systemd-analyze emits without failing its own exit code.
warning_re='Unknown key name|Unknown section|Unknown lvalue|ignoring|Failed to'

status=0
for unit in "$@"; do
    output=$(systemd-analyze verify "$unit" 2>&1)
    rc=$?
    if [ "$rc" -ne 0 ] || echo "$output" | grep -Eq "$warning_re"; then
        echo "verify-units: FAIL $unit" >&2
        [ -n "$output" ] && echo "$output" >&2
        status=1
    fi
done

exit "$status"
