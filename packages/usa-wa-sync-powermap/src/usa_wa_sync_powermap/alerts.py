"""Operator email alerts via the exe.dev email gateway (#85).

The sidecar is a ``Restart=`` service, so it never routes through the #49
``OnFailure=usa-wa-notify-failure@`` handler — a failure streak inside the
running daemon must email the operator itself. This reuses only the gateway
POST from ``scripts/notify-failure.sh`` (a documented VM feature — no MTA or
SMTP creds needed); the shell handler's systemd unit introspection
(``systemctl show``, ``InvocationID`` journal scoping) is meaningless for an
in-process alert.

Fail-closed like the script: :func:`build_alert` returns ``None`` when the
recipient (``USA_WA_ALERT_EMAIL``) is unset, and the daemon logs the gap loudly
at startup rather than silently dropping alerts.
"""

from collections.abc import Awaitable, Callable

import httpx

#: exe.dev email gateway — link-local metadata endpoint, POST only, recipient
#: must be a team member (see https://exe.dev/docs/send-email.md).
GATEWAY_URL = "http://169.254.169.254/gateway/email/send"

#: Generous for a link-local endpoint; matches notify-failure.sh's --max-time.
_TIMEOUT_SECONDS = 20.0


async def send_email_alert(
    recipient: str,
    subject: str,
    body: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """POST one email to the gateway; raise on a non-2xx response.

    A fresh client per send — alerts are rare (once per failure streak), so
    connection reuse is worthless next to the simplicity. ``transport`` is the
    test seam (httpx.MockTransport).
    """
    async with httpx.AsyncClient(transport=transport, timeout=_TIMEOUT_SECONDS) as client:
        response = await client.post(
            GATEWAY_URL,
            json={"to": recipient, "subject": subject, "body": body},
        )
        response.raise_for_status()


def build_alert(
    recipient: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Callable[[str, str], Awaitable[None]] | None:
    """Bind a recipient into the Sidecar's ``alert`` callable shape.

    Returns ``None`` when no recipient is configured — the caller decides how
    loudly to surface that (the daemon warns at startup).
    """
    if not recipient:
        return None

    async def _alert(subject: str, body: str) -> None:
        await send_email_alert(recipient, subject, body, transport=transport)

    return _alert
