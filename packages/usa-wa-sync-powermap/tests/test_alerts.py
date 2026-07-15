"""Operator email alert helper tests (#85).

The sidecar's failure-streak alert posts to the exe.dev email gateway — the same
transport ``scripts/notify-failure.sh`` uses for the oneshot units, reimplemented
in-process because the shell handler is coupled to systemd unit introspection.
"""

import json

import httpx
import pytest

from usa_wa_sync_powermap.alerts import GATEWAY_URL, build_alert, send_email_alert


def _capture_transport(captured: list[httpx.Request], status_code: int = 200):
    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code)

    return httpx.MockTransport(_handler)


async def test_send_email_alert_posts_gateway_payload():
    captured: list[httpx.Request] = []
    transport = _capture_transport(captured)

    await send_email_alert("ops@example.com", "subject line", "body text", transport=transport)

    (request,) = captured
    assert str(request.url) == GATEWAY_URL
    assert request.method == "POST"
    payload = json.loads(request.content)
    assert payload == {"to": "ops@example.com", "subject": "subject line", "body": "body text"}


async def test_send_email_alert_raises_on_gateway_error():
    """A gateway error surfaces to the caller — the Sidecar swallows + logs it."""
    transport = _capture_transport([], status_code=500)

    with pytest.raises(httpx.HTTPStatusError):
        await send_email_alert("ops@example.com", "s", "b", transport=transport)


async def test_build_alert_none_when_recipient_unset():
    """Fail-closed like notify-failure.sh: no recipient → no alert callable (the
    daemon logs the gap loudly at startup rather than silently dropping alerts)."""
    assert build_alert(None) is None
    assert build_alert("") is None


async def test_build_alert_binds_recipient():
    captured: list[httpx.Request] = []
    transport = _capture_transport(captured)
    alert = build_alert("ops@example.com", transport=transport)
    assert alert is not None

    await alert("subj", "body")

    payload = json.loads(captured[0].content)
    assert payload["to"] == "ops@example.com"
