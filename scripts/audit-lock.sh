#!/usr/bin/env bash
# Audit uv.lock against OSV and report what it finds. Never fail (#499).
#
# Dependabot is the update mechanism (.github/dependabot.yml: monthly, one
# grouped PR, landed in the patching-hosts window). This report is what shows a
# disclosure between its runs; scripts/pre-ship.sh runs it before every ship.
# It is report-only on purpose. A gate that failed on a finding would block
# every unrelated ship on someone else's disclosure date (CannObserv/broker#86,
# #79). So every exit is 0:
#   uv exit 1 (findings)        -> "WARNING: uv.lock advisories"
#   any other non-zero exit     -> "WARNING: uv audit did not run", never clean
#
# --frozen reads uv.lock as committed: the report describes the lock being
# shipped. `uv audit` is a preview command; uv 0.11 names the feature `audit`
# (broker's `audit-command` is an unknown flag here and exits 2).
# Ported from broker's audit_lock.sh less its GitHub Actions branches: no workflow here
# runs it. scripts/tests/test_audit_lock.py pins all of it.
set -u

cd "$(dirname "$0")/.."

out=$(uv audit --frozen --preview-features audit 2>&1)
rc=$?

printf '%s\n' "$out"

case $rc in
0) ;;
1) echo "WARNING: uv.lock advisories: uv audit found advisories in uv.lock (listed above). Report-only; bump with uv lock --upgrade-package <name>, or let the monthly Dependabot PR carry it (#499)." >&2 ;;
*) echo "WARNING: uv audit did not run: uv audit exited $rc; the lock was not audited (output above)." >&2 ;;
esac
exit 0
