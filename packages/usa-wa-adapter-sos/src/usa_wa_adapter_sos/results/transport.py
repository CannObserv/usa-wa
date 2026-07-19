"""Transport — ``httpx`` client for the WA SOS ``results.vote.wa.gov`` legislative results.

The SOS retired the ``eledataweb.votewa.gov`` *Export To Excel* filings export for elections
**2020+** (migrated to Power BI), but publishes each general election's certified results at
``results.vote.wa.gov/results/<YYYYMMDD>/`` — including a **Legislative** offices CSV carrying the
ballot ``Position 1/2`` this source exists to supply (2008→present, incl. the current cycle).

The export filename is **not derivable** — recent years are ``export/<date>_Legislative.csv`` but
older ones carry a certification timestamp (``export/20121106_Legislative_20121205_1451.csv``), so
the client **traverses** the election's ``export.html`` index to discover the actual href, then
fetches it (the CSV 302s to a lowercase path — redirects are followed). Like the sibling filings
transport it mirrors the :class:`WireFetch` contract: the pristine CSV body is archived + hashed
(#54); the derived parse is a convenience so Phase B doesn't re-decode.

A central courtesy min-interval gate (:data:`_RESULTS_LIMITER`, the #77 pattern) spaces calls to
the ``results.vote.wa.gov`` host — a *distinct* host from the filings source, hence its own limiter
and env knob ``USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL`` (default 1.0s, ``0`` disables).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from usa_wa_adapter_sos.ratelimit import AsyncRateLimiter, env_float

#: The WA SOS election-results host.
RESULTS_BASE_URL = "https://results.vote.wa.gov"

#: Env knob for the results-host courtesy floor (seconds); overridable via
#: :func:`configure_results_rate_limit`. Distinct from the filings host's knob.
RESULTS_MIN_INTERVAL_ENV = "USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL"

#: Default courtesy floor between any two ``results.vote.wa.gov`` calls — gentle (a low-QPS
#: government site; the harvest is a handful of two-call fetches).
DEFAULT_RESULTS_MIN_REQUEST_INTERVAL = 1.0


@dataclass(frozen=True)
class WireFetch:
    """An archival fetch result: the pristine results-CSV wire bytes plus the derived row parse.

    ``wire`` is the raw CSV body — the provenance source of truth archived + hashed (#54);
    ``records`` is the decoded row list, derivative (``wire`` wins on any disagreement).
    """

    records: list[dict[str, Any]]
    wire: bytes
    content_type: str


class LegislativeExportNotFound(LookupError):
    """An election's ``export.html`` index carried no Legislative results CSV link (a year the
    harvest skips-and-logs, distinct from an HTTP error on the index itself)."""


def parse_legislative_results(wire: bytes) -> list[dict[str, Any]]:
    """Decode an **archived** legislative-results CSV body offline back into row dicts (#56 cache
    path). UTF-8 BOM-tolerant; the header row (``Race``/``Candidate``/``Party``/…) becomes keys."""
    text = wire.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


#: The one shared limiter every ``results.vote.wa.gov`` GET passes through (#77).
_RESULTS_LIMITER = AsyncRateLimiter(
    env_float(RESULTS_MIN_INTERVAL_ENV, DEFAULT_RESULTS_MIN_REQUEST_INTERVAL)
)


def configure_results_rate_limit(min_interval: float) -> None:
    """Set the central ``results.vote.wa.gov`` min-interval (seconds) — maps a harvest's
    ``--pause-seconds``; the test suite zeroes it via an autouse fixture."""
    _RESULTS_LIMITER.set_interval(min_interval)


def general_election_date(election_year: int) -> str:
    """The ``YYYYMMDD`` of a year's WA **general** election — the first Tuesday after the first
    Monday of November. ``2024`` → ``"20241105"``, ``2012`` → ``"20121106"``."""
    d = date(election_year, 11, 1)
    while d.weekday() != 0:  # advance to November's first Monday (Monday == 0)
        d += timedelta(days=1)
    return (d + timedelta(days=1)).strftime("%Y%m%d")  # the Tuesday after it


#: The Legislative results CSV href inside an election's ``export.html`` — matches both the clean
#: ``export/<date>_Legislative.csv`` and the certification-timestamped variant; excludes the
#: ``Legislative.html`` results page and the ``.xml`` sibling.
_LEGISLATIVE_HREF = re.compile(r"export/\d{8}_Legislative[^\"']*\.csv", re.IGNORECASE)


def legislative_href(index_html: str) -> str | None:
    """The relative Legislative CSV href discovered in an ``export.html`` index, or ``None``."""
    match = _LEGISLATIVE_HREF.search(index_html)
    return match.group(0) if match else None


class SOSResultsClient:
    """Thin async ``results.vote.wa.gov`` reader for a general election's Legislative CSV."""

    def __init__(self, *, base_url: str = RESULTS_BASE_URL, timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def export_index_url(self, election_date: str) -> str:
        """The ``export.html`` index URL for a ``YYYYMMDD`` election date."""
        return f"{self._base_url}/results/{election_date}/export.html"

    async def fetch_legislative_results(self, election_year: int) -> WireFetch:
        """Traverse the election's ``export.html`` → fetch the Legislative CSV (archived + hashed,
        #54). Each GET passes the courtesy gate; follows the CSV's lowercase redirect. Raises
        ``httpx.HTTPStatusError`` on a non-2xx (e.g. an unheld year's index 404s) or
        :class:`LegislativeExportNotFound` when the index has no Legislative CSV."""
        election_date = general_election_date(election_year)
        index_url = self.export_index_url(election_date)
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            await _RESULTS_LIMITER.acquire()
            index = await client.get(index_url)
            index.raise_for_status()
            href = legislative_href(index.text)
            if href is None:
                raise LegislativeExportNotFound(f"no Legislative results CSV in {index_url}")
            await _RESULTS_LIMITER.acquire()
            response = await client.get(f"{self._base_url}/results/{election_date}/{href}")
            response.raise_for_status()
            wire = response.content
            content_type = response.headers.get("content-type", "text/csv")
        return WireFetch(
            records=parse_legislative_results(wire), wire=wire, content_type=content_type
        )
