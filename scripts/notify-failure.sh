#!/usr/bin/env bash
# OnFailure= handler for usa-wa's unattended oneshots (issue #49).
#
# Started by `usa-wa-notify-failure@<failed-unit>.service` when a failable
# oneshot — usa-wa-migrate, usa-wa-wsl-refresh, usa-wa-reconcile-committee-active
# — exits non-zero or times out. Emails the operator via the exe.dev email
# gateway (a documented VM feature: https://exe.dev/docs/send-email.md), so it
# needs no MTA and no SMTP creds on this single headless VM.
#
# The reconcile CLI's exit-code contract (#44: 1 rejected / 2 auth / 3 guardrail
# abort) is surfaced in the subject line so the operator can triage without
# opening the journal — the whole point #49 makes about the codes being
# "observable" only if something is watching.
#
# Fail-closed: a missing recipient aborts loudly rather than silently dropping
# the alert. A failed send is logged but does not retry (no OnFailure on the
# handler — see the unit) since the original failure is already in the journal.
#
# Usage: notify-failure.sh <failed-unit-name>   (systemd passes %i)
set -uo pipefail

unit="${1:?notify-failure: missing failed-unit argument (expected systemd %i)}"

if [ -z "${USA_WA_ALERT_EMAIL:-}" ]; then
    echo "notify-failure: USA_WA_ALERT_EMAIL unset; cannot alert for ${unit}" >&2
    exit 1
fi

# Exit status of the unit's main process. `systemctl show` works for any user
# (no journal read needed) and yields the raw code behind the failure.
exit_code=$(systemctl show "$unit" -p ExecMainStatus --value 2>/dev/null)
result=$(systemctl show "$unit" -p Result --value 2>/dev/null)

# Last lines of *this* failed run for context — scoped by the unit's current
# InvocationID so a sparse failing run can't surface stale lines from prior
# (successful) invocations. exedev is in `adm`, so it can read system-unit logs;
# tolerate emptiness regardless (degrade to code-only).
invocation=$(systemctl show "$unit" -p InvocationID --value 2>/dev/null)
if [ -n "$invocation" ]; then
    tail_lines=$(journalctl _SYSTEMD_INVOCATION_ID="$invocation" -n 25 --no-pager -o short-iso 2>/dev/null || true)
else
    tail_lines=$(journalctl -u "$unit" -n 25 --no-pager -o short-iso 2>/dev/null || true)
fi

host=$(hostname 2>/dev/null || echo "unknown")
subject="[usa-wa] ${unit} failed (exit ${exit_code:-unknown}, ${result:-unknown})"
body=$(printf 'Unit:   %s\nHost:   %s\nResult: %s\nExit:   %s\n\n--- last journal lines ---\n%s\n' \
    "$unit" "$host" "${result:-unknown}" "${exit_code:-unknown}" "${tail_lines:-(none available)}")

# exe.dev email gateway — link-local metadata endpoint, POST only, recipient must
# be you / a team member (USA_WA_ALERT_EMAIL). jq -nc --arg builds a JSON-safe
# payload (each field escaped). Guard emptiness: without set -e a jq failure
# would otherwise post an empty body and surface only as an opaque gateway error.
payload=$(jq -nc --arg to "$USA_WA_ALERT_EMAIL" --arg subject "$subject" --arg body "$body" \
    '{to: $to, subject: $subject, body: $body}')
if [ -z "$payload" ]; then
    echo "notify-failure: failed to build JSON payload for ${unit} (jq error)" >&2
    exit 1
fi

response=$(curl -fsS --max-time 20 \
    -X POST http://169.254.169.254/gateway/email/send \
    -H "Content-Type: application/json" \
    -d "$payload" 2>&1)
rc=$?

if [ "$rc" -ne 0 ]; then
    echo "notify-failure: gateway POST failed for ${unit} (curl rc=${rc}): ${response}" >&2
    exit 1
fi

echo "notify-failure: alerted ${USA_WA_ALERT_EMAIL} for ${unit} (exit ${exit_code:-?})"
