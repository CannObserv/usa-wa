"""Report each nightly backup run to its dead-man monitor on co-status (#455).

A backup that fails loudly still says nothing when it stops running: a disabled
timer, a unit never installed on a new host, a job wedged before systemd records a
failure — all produce zero failures and zero traffic. So the job checks in on
**every** run, success or not, and co-status alarms when a check-in fails to arrive.
Ported from CannObserv/watcher's ``src/ops/checkin.py``; co-status kept notifier's
check-in contract exactly.

``POST /api/v1/monitors/{id}/checkin`` with ``{"status": "ok"|"alert",
"variables": {...}}``. An ``ok`` dispatches nothing and resets the timer; an
``alert`` renders the monitor's template against ``variables``. Retry-safe by
contract: a replay overwrites the previous check-in.

**Failure never propagates.** A check-in never raises and never changes the job's
exit status: a monitoring path that fails the thing it monitors trains an operator
to ignore both. A check-in that does not land is the monitor's to notice.

**The key is a credential, never an environment variable.** The base URL and the
monitor id are configuration, in ``/etc/usa-wa/backup.env``; the key is the unit's
``checkin-key`` credential, read by systemd from the root-only
``/etc/usa-wa/backup-checkin.key`` and handed to the run as a private file under
``$CREDENTIALS_DIRECTORY`` — in no process environment, so in no child's either.
docs/RECOVERY.md § The dead-man monitor is the provisioning.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from clearinghouse_core.logging import get_logger
from usa_wa_api.backup.gcs import CREDENTIALS_DIRECTORY_ENV

logger = get_logger(__name__)

BASE_URL_ENV = "USA_WA_BACKUP_CHECKIN_BASE_URL"
MONITOR_ID_ENV = "USA_WA_BACKUP_MONITOR_ID"
#: The key's file under ``$CREDENTIALS_DIRECTORY`` — the unit's ``LoadCredential=`` name.
KEY_CREDENTIAL = "checkin-key"
_KEY_LABEL = f"the {KEY_CREDENTIAL} credential"
#: Under co-status's own 8-second answer budget plus the network's share.
TIMEOUT_SECONDS = 10.0
#: One immediate retry on a transport error or a 5xx: a dropped check-in reads as a
#: dead job, and the replay is harmless.
_ATTEMPTS = 2
#: Monitor ids are ULIDs; the value becomes a URL path segment.
_MONITOR_ID_RE = re.compile(r"^[0-9A-Za-z]+$")

Status = Literal["ok", "alert"]
Post = Callable[[str, dict, dict, float], int]


def _http_post(url: str, payload: dict, headers: dict, timeout: float) -> int:
    """One POST, returning the status code. The seam the tests replace."""
    with httpx.Client(timeout=timeout) as client:
        return client.post(url, json=payload, headers=headers).status_code


def _read_key(environ: Mapping[str, str]) -> str:
    """The key credential's contents, or "" when there is none to read.

    Under the unit the file always exists — a missing source fails the start — and
    is empty until the monitor does. Outside one there is no directory. Anything
    else (a directory where the file should be, a permission error) propagates to
    :func:`post_checkin`'s guard.
    """
    directory = environ.get(CREDENTIALS_DIRECTORY_ENV, "").strip()
    if not directory:
        return ""
    try:
        return (Path(directory) / KEY_CREDENTIAL).read_text().strip()
    except FileNotFoundError:
        return ""


def _is_http_base(base: str) -> bool:
    """An http(s) URL with a host and, if any, a numeric port. ``urlsplit`` raises
    on a bad bracket and ``.port`` on a bad port; both are a typo."""
    try:
        parts = urlsplit(base)
        _ = parts.port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def post_checkin(
    status: Status,
    variables: dict[str, Any],
    *,
    environ: Mapping[str, str],
    post: Post = _http_post,
) -> bool:
    """Report this run; return whether the check-in landed. Never raises.

    The guarantee is this wrapper's, not the callee's care: whatever escapes the
    configuration checks — a non-ASCII key failing header encoding, a URL httpx
    parses differently from ``urlsplit`` — is logged by type (never by message,
    which may quote the key) and reported as a check-in that did not land.
    """
    try:
        return _post_checkin(status, variables, environ=environ, post=post)
    except Exception as exc:
        logger.error("backup_checkin_failed", extra={"error": type(exc).__name__})
        return False


def _config_error(error: str) -> bool:
    logger.error("backup_checkin_config_error", extra={"error": error})
    return False


def _post_checkin(
    status: Status, variables: dict[str, Any], *, environ: Mapping[str, str], post: Post
) -> bool:
    base = environ.get(BASE_URL_ENV, "").strip()
    monitor_id = environ.get(MONITOR_ID_ENV, "").strip()
    api_key = _read_key(environ)
    present = {
        BASE_URL_ENV: bool(base),
        MONITOR_ID_ENV: bool(monitor_id),
        _KEY_LABEL: bool(api_key),
    }
    if not any(present.values()):
        logger.warning(
            "backup_checkin_unconfigured",
            extra={"note": "a backup that stops running will not be noticed"},
        )
        return False
    if not all(present.values()):
        missing = ", ".join(name for name, there in present.items() if not there)
        return _config_error(f"half-configured, missing {missing}; set all three or none")
    if not _is_http_base(base):
        return _config_error(f"{BASE_URL_ENV} is not an http(s) URL with a host")
    if not _MONITOR_ID_RE.match(monitor_id):
        return _config_error(f"{MONITOR_ID_ENV} is not a bare monitor id")

    url = f"{base.rstrip('/')}/api/v1/monitors/{monitor_id}/checkin"
    payload = {"status": status, "variables": variables}
    headers = {"X-API-Key": api_key}
    failure = ""
    for _ in range(_ATTEMPTS):
        try:
            code = post(url, payload, headers, TIMEOUT_SECONDS)
        except httpx.TransportError as exc:
            failure = f"{type(exc).__name__}: {exc}"
            continue
        if 200 <= code < 300:
            logger.info("backup_checkin_sent", extra={"status": status})
            return True
        if code < 500:
            logger.warning(
                "backup_checkin_rejected",
                extra={"http_status": code, "hint": "check the monitor id and the key"},
            )
            return False
        failure = f"HTTP {code}"
    logger.warning("backup_checkin_failed", extra={"error": failure})
    return False
