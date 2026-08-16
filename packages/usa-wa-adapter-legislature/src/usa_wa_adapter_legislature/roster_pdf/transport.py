"""Transport for the roster PDF (#225) — fetch, hash, and re-discover a rotated URL.

The document lives at an opaque CMS media key on ``leg.wa.gov``::

    https://leg.wa.gov/media/s4gf4suc/members-of-the-legislature-1889-2025.pdf

Two properties shape this module, both verified live 2026-08-14:

**No cache validators.** The response carries no ``ETag``, no ``Last-Modified`` and no
``Cache-Control`` (Microsoft-IIS/10.0), so conditional GET is unavailable and change detection is
a full fetch plus a content hash. At the quarterly cadence this source runs on, that is ~23MB a
year — cheap enough that the absence of validators is a non-issue rather than a reason to poll
harder.

**The URL is the fragile part, not the content.** ``s4gf4suc`` is a CMS-minted key; a re-publish
is expected to mint a new one, and the filename carries the edition years. So a **404 means
re-discover the href** from the Legislative Information Center index — the same traversal the SOS
results transport does for its varying export filenames — while any other status is a genuine
outage and propagates. Treating a 500 as a rotated key would mask a real failure; treating a 404
as an outage would strand the source permanently on the next re-publish.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import httpx

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
    ) -> None:
        self.url = url
        self.index_url = index_url
        self._timeout = timeout

    async def fetch_roster(self) -> RosterFetch:
        """Fetch the roster PDF, re-discovering the href if the known URL has rotated away.

        Raises ``httpx.HTTPStatusError`` on any non-404 error status and
        :class:`RosterUnavailable` when a 404 cannot be resolved to a new href.
        """
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
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
