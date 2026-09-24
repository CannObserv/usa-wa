"""Transport for the roster PDF (#225) — fetch, hash, and re-discover a rotated URL.

The document lives at an opaque CMS media key on ``leg.wa.gov``::

    https://leg.wa.gov/media/s4gf4suc/members-of-the-legislature-1889-2025.pdf

Two properties shape this module, both verified live 2026-08-14:

**No cache validators.** The response carries no ``ETag``, no ``Last-Modified`` and no
``Cache-Control`` (Microsoft-IIS/10.0), so conditional GET is unavailable and change detection is
a full fetch plus a content hash. At the monthly edition re-check this source runs on (#237),
that is ~69MB a year — cheap enough that the absence of validators is a non-issue rather than a
reason to poll harder.

**The URL is the fragile part, not the content.** ``s4gf4suc`` is a CMS-minted key; a re-publish
is expected to mint a new one, and the filename carries the edition years. So a **404 means
re-discover the href** from the Legislative Information Center index — the same traversal the SOS
results transport does for its varying export filenames — while any other status is a genuine
outage and propagates. Treating a 500 as a rotated key would mask a real failure; treating a 404
as an outage would strand the source permanently on the next re-publish.

**A courtesy gate on every request (#236)** — the #77 pattern every other source already follows.
One host limiter (:data:`_LEG_LIMITER`, knob ``USA_WA_LEG_MIN_REQUEST_INTERVAL``) is enforced by
an httpx request hook, so re-discovery's three GETs and any redirect hop are all spaced. Like
its siblings it is in-process: it spaces the requests *within* a run, and the first request of
a fresh process never waits. It does not throttle repeated ``--force`` invocations.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass

import httpx

from usa_wa_adapter_legislature.ratelimit import RateLimiter, env_float

#: The ``leg.wa.gov`` host.
LEG_BASE_URL = "https://leg.wa.gov"

#: The roster's current URL (2025-06-05 revision). A starting point, not a durable identifier --
#: see the module docstring on media-key rotation.
DEFAULT_ROSTER_URL = f"{LEG_BASE_URL}/media/s4gf4suc/members-of-the-legislature-1889-2025.pdf"

#: Where to look when the known URL 404s.
DEFAULT_INDEX_URL = f"{LEG_BASE_URL}/about-the-legislature/legislative-information-center/"

#: Any media href whose filename is a *Members of the Legislature* edition. Deliberately loose on
#: both the media key and the edition years: the point is to survive a re-publish that changes
#: both (``.../ZZZZZZZZ/members-of-the-legislature-1889-2027.pdf``).
_ROSTER_HREF = re.compile(
    r"/media/[^\"'/]+/members-of-the-legislature-\d{4}-\d{4}\.pdf", re.IGNORECASE
)


#: Courtesy floor between any two ``leg.wa.gov`` requests (#236), the #77 pattern. A different
#: host from WSL's ``wslwebservices.leg.wa.gov``, so its own instance + knob. 1.0s matches the
#: SOS hosts: a low-QPS government site, and a run is one GET (three on re-discovery). ``0``
#: disables; a harvest's ``--pause-seconds`` overrides it via :func:`configure_leg_rate_limit`.
DEFAULT_LEG_MIN_REQUEST_INTERVAL = 1.0

LEG_MIN_INTERVAL_ENV = "USA_WA_LEG_MIN_REQUEST_INTERVAL"


def _env_min_interval() -> float:
    """Read ``USA_WA_LEG_MIN_REQUEST_INTERVAL`` (default on unset/malformed)."""
    return env_float(LEG_MIN_INTERVAL_ENV, DEFAULT_LEG_MIN_REQUEST_INTERVAL)


#: The one limiter every ``leg.wa.gov`` request passes through.
_LEG_LIMITER = RateLimiter(_env_min_interval())


def configure_leg_rate_limit(min_interval: float) -> None:
    """Set the ``leg.wa.gov`` min-interval (seconds). Maps a harvest's ``--pause-seconds`` onto
    the host limiter; the test suite zeroes it via an autouse fixture."""
    _LEG_LIMITER.set_interval(min_interval)


class RosterUnavailable(LookupError):
    """The known URL 404'd and no roster href could be discovered on the index page.

    Distinct from an HTTP error: this is "the document moved and we cannot find where", which
    needs an operator to re-point the source, not a retry.
    """


@dataclass(frozen=True)
class RosterFetch:
    """An archival fetch: the pristine PDF bytes plus the hash change detection turns on."""

    wire: bytes
    sha256: str
    content_type: str
    url: str


def roster_href(index_html: str) -> str | None:
    """The roster PDF's href discovered on an index page, or ``None``."""
    match = _ROSTER_HREF.search(index_html)
    return match.group(0) if match else None


class RosterPdfClient:
    """Thin async reader for the roster PDF, with 404-triggered href re-discovery."""

    def __init__(
        self,
        *,
        url: str = DEFAULT_ROSTER_URL,
        index_url: str = DEFAULT_INDEX_URL,
        timeout: float = 120.0,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.url = url
        self.index_url = index_url
        self._timeout = timeout
        self._limiter = limiter if limiter is not None else _LEG_LIMITER

    async def fetch_roster(self) -> RosterFetch:
        """Fetch the roster PDF, re-discovering the href if the known URL has rotated away.

        Raises ``httpx.HTTPStatusError`` on any non-404 error status and
        :class:`RosterUnavailable` when a 404 cannot be resolved to a new href.
        """
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            event_hooks={"request": [self._gate]},
        ) as client:
            response = await client.get(self.url)
            if response.status_code == 404:
                response = await self._rediscover(client)
            response.raise_for_status()
            wire = response.content
            return RosterFetch(
                wire=wire,
                sha256=hashlib.sha256(wire).hexdigest(),
                content_type=response.headers.get("content-type", "application/pdf"),
                url=str(response.request.url),
            )

    async def _gate(self, request: httpx.Request) -> None:
        """Request hook: wait on the host limiter before **every** request — the known URL, the
        index and the moved href on re-discovery, and any redirect hop, none of which a call
        site sees individually. :meth:`RateLimiter.acquire` sleeps with ``time.sleep``, so it
        runs in a worker thread rather than stalling the event loop."""
        await asyncio.to_thread(self._limiter.acquire)

    async def _rediscover(self, client: httpx.AsyncClient) -> httpx.Response:
        """Resolve a rotated media key from the Legislative Information Center index."""
        index = await client.get(self.index_url)
        index.raise_for_status()
        href = roster_href(index.text)
        if href is None:
            raise RosterUnavailable(
                f"{self.url} returned 404 and no roster href was found at {self.index_url}"
            )
        return await client.get(f"{LEG_BASE_URL}{href}")
