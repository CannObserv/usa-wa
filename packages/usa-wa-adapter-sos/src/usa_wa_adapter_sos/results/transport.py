"""Transport — ``httpx`` client for the WA SOS legislative results: ``results.vote.wa.gov``
through 2025, the ``results.votewa.gov`` platform from 2026 (#575).

The SOS retired the ``eledataweb.votewa.gov`` *Export To Excel* filings export for elections
**2020+** (migrated to Power BI), but published each general election's results from 2008 to 2025
at ``results.vote.wa.gov/results/<YYYYMMDD>/`` — including a **Legislative** offices CSV carrying
the ballot ``Position 1/2`` this source exists to supply.

The export filename is **not derivable** — recent years are ``export/<date>_Legislative.csv`` but
older ones carry a certification timestamp (``export/20121106_Legislative_20121205_1451.csv``), so
the client **traverses** the election's ``export.html`` index to discover the actual href, then
fetches it (the CSV 302s to a lowercase path — redirects are followed). Like the sibling filings
transport it mirrors the :class:`WireFetch` contract: the pristine CSV body is archived + hashed
(#54); the derived parse is a convenience so a caller doesn't re-decode.

**From 2026 the SOS publishes on a vendor platform instead (#575).** ``results.vote.wa.gov``
keeps every earlier election but 404s a 2026+ one, which lives only at ``results.votewa.gov``. So
an old index the host disowns — a 404, or a redirect off the host — falls back to the platform: its
election JSON (``/results/public/api/elections/washington/<YYYYMMDD>``) lists the reports, whose
blob names carry a per-publish GUID and so are discovered, never derived; the *All Results* workbook
(:func:`workbook_report`) is fetched from ``/cdn/results/<jurisdictionId>/<blob>`` and archived as
the wire, parsed by :mod:`usa_wa_adapter_sos.results.workbook`. A platform fetch reports its
election-JSON URL as :attr:`WireFetch.url`, the traversal's real entry point, so the harvest records
that rather than the old index it began from (#54); an old-site fetch reports none.

A central courtesy min-interval gate (:data:`_RESULTS_LIMITER`, the #77 pattern) spaces calls to
the results hosts — *distinct* from the filings source, hence its own limiter and env knob
``USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL`` (default 1.0s, ``0`` disables). One gate spans both
results hosts: a fallback is one more call in the same sequential harvest.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from usa_wa_adapter_sos.ratelimit import AsyncRateLimiter, env_float
from usa_wa_adapter_sos.results.workbook import is_workbook, legislative_rows
from usa_wa_common.elections import general_election_day

#: The WA SOS election-results host (every general election before 2026).
RESULTS_BASE_URL = "https://results.vote.wa.gov"

#: The vendor results platform the SOS publishes on from 2026 (#575).
PLATFORM_BASE_URL = "https://results.votewa.gov"

#: The platform's state slug in its election-JSON path.
PLATFORM_STATE = "washington"

#: Env knob for the results-host courtesy floor (seconds); overridable via
#: :func:`configure_results_rate_limit`. Distinct from the filings host's knob.
RESULTS_MIN_INTERVAL_ENV = "USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL"

#: Default courtesy floor between any two ``results.vote.wa.gov`` calls — gentle (a low-QPS
#: government site; the harvest is a handful of two-call fetches).
DEFAULT_RESULTS_MIN_REQUEST_INTERVAL = 1.0


@dataclass(frozen=True)
class WireFetch:
    """An archival fetch result: the pristine results-CSV wire bytes plus the derived row parse.

    ``wire`` is the raw CSV (or, from the platform, workbook) body — the provenance source of
    truth archived + hashed (#54); ``records`` is the decoded row list, derivative (``wire`` wins
    on any disagreement). ``url`` is set only when the wire came from somewhere other than the
    old export index the harvest records by default — the platform's election JSON (#575).
    """

    records: list[dict[str, Any]]
    wire: bytes
    content_type: str
    url: str | None = None


class LegislativeExportNotFound(LookupError):
    """An election's ``export.html`` index carried no Legislative results CSV link, or its
    platform election listed no workbook (a year the harvest skips-and-logs, distinct from an
    HTTP error on the index itself)."""


def parse_legislative_results(wire: bytes) -> list[dict[str, Any]]:
    """Decode an **archived** legislative-results wire offline back into row dicts (#56 cache
    path). A platform workbook (its zip magic, #575) reads through
    :func:`~usa_wa_adapter_sos.results.workbook.legislative_rows`; a CSV is UTF-8 BOM-tolerant,
    its header row (``Race``/``Candidate``/``Party``/…) becoming keys. Both yield the same keys."""
    if is_workbook(wire):
        return legislative_rows(wire)
    text = wire.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


#: The one shared limiter every ``results.vote.wa.gov`` GET passes through (#77).
_RESULTS_LIMITER = AsyncRateLimiter(
    env_float(RESULTS_MIN_INTERVAL_ENV, DEFAULT_RESULTS_MIN_REQUEST_INTERVAL)
)


def configure_results_rate_limit(min_interval: float) -> None:
    """Set the central ``results.vote.wa.gov`` min-interval (seconds).

    No production caller since #412 PR F deleted the harvest whose ``--pause-seconds`` it
    mapped; kept (#471) as the seam the test suite's autouse fixture zeroes the gate through."""
    _RESULTS_LIMITER.set_interval(min_interval)


def general_election_date(election_year: int) -> str:
    """The ``YYYYMMDD`` of a year's WA **general** election — the first Tuesday after the first
    Monday of November. ``2024`` → ``"20241105"``, ``2012`` → ``"20121106"``."""
    return general_election_day(election_year).strftime("%Y%m%d")


#: The Legislative results CSV href inside an election's ``export.html`` — matches both the clean
#: ``export/<date>_Legislative.csv`` and the certification-timestamped variant; excludes the
#: ``Legislative.html`` results page and the ``.xml`` sibling.
_LEGISLATIVE_HREF = re.compile(r"export/\d{8}_Legislative[^\"']*\.csv", re.IGNORECASE)


def legislative_href(index_html: str) -> str | None:
    """The relative Legislative CSV href discovered in an ``export.html`` index, or ``None``."""
    match = _LEGISLATIVE_HREF.search(index_html)
    return match.group(0) if match else None


#: A report name for the whole-election results, whatever its suffix (``All Results Excel``).
_ALL_RESULTS = re.compile(r"\ball\s+results\b", re.IGNORECASE)


def workbook_report(election: dict[str, Any]) -> str | None:
    """The blob name of a platform election's results workbook, or ``None`` if it lists none.

    Prefers the report named for *all* results; else the first ``.xlsx`` listed (the other
    published workbook carries the same summary sheet). Never an exact report-name match."""
    workbooks = [
        report
        for category in election.get("publicReportCategories") or []
        for report in category.get("reports") or []
        if str(report.get("blobName") or "").lower().endswith(".xlsx")
    ]
    preferred = [r for r in workbooks if _ALL_RESULTS.search(str(r.get("reportName") or ""))]
    chosen = (preferred or workbooks or [None])[0]
    return None if chosen is None else chosen["blobName"]


class SOSResultsClient:
    """Thin async reader for a general election's Legislative results: the ``results.vote.wa.gov``
    CSV, else (2026+, #575) the ``results.votewa.gov`` workbook."""

    def __init__(
        self,
        *,
        base_url: str = RESULTS_BASE_URL,
        platform_base_url: str = PLATFORM_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._platform_base_url = platform_base_url.rstrip("/")
        self._timeout = timeout

    def export_index_url(self, election_date: str) -> str:
        """The ``export.html`` index URL for a ``YYYYMMDD`` election date."""
        return f"{self._base_url}/results/{election_date}/export.html"

    def platform_election_url(self, election_date: str) -> str:
        """The platform's election-JSON URL for a ``YYYYMMDD`` election date."""
        return (
            f"{self._platform_base_url}/results/public/api/elections/"
            f"{PLATFORM_STATE}/{election_date}"
        )

    async def fetch_legislative_results(self, election_year: int) -> WireFetch:
        """Traverse the election's ``export.html`` → fetch the Legislative CSV (archived + hashed,
        #54). Each GET passes the courtesy gate; follows the CSV's lowercase redirect. An index
        the old host doesn't hold (:meth:`_not_held_here`) falls back to the platform workbook
        (#575). Raises ``httpx.HTTPStatusError`` on a
        non-2xx (e.g. a year neither site holds) or :class:`LegislativeExportNotFound` when the
        index has no Legislative CSV or the platform election no workbook."""
        election_date = general_election_date(election_year)
        index_url = self.export_index_url(election_date)
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            await _RESULTS_LIMITER.acquire()
            index = await client.get(index_url)
            if self._not_held_here(index):
                return await self._fetch_platform_workbook(client, election_date)
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

    def _not_held_here(self, index: httpx.Response) -> bool:
        """Whether the old host disowned this election's index: a 404, or a redirect that left
        the host (it already 302s ``/results/current/`` to the platform). Any other error still
        raises, so a 5xx never sends a held year to the platform."""
        if index.status_code == httpx.codes.NOT_FOUND:
            return True
        return index.url.host != httpx.URL(self._base_url).host

    async def _fetch_platform_workbook(
        self, client: httpx.AsyncClient, election_date: str
    ) -> WireFetch:
        """The platform's election JSON → its results workbook (#575)."""
        election_url = self.platform_election_url(election_date)
        await _RESULTS_LIMITER.acquire()
        election_response = await client.get(election_url)
        election_response.raise_for_status()
        election = election_response.json()
        blob = workbook_report(election)
        jurisdiction = election.get("jurisdictionId")
        if blob is None or not jurisdiction:
            raise LegislativeExportNotFound(f"no results workbook in {election_url}")
        await _RESULTS_LIMITER.acquire()
        response = await client.get(
            f"{self._platform_base_url}/cdn/results/{jurisdiction}/{quote(blob)}"
        )
        response.raise_for_status()
        wire = response.content
        return WireFetch(
            records=parse_legislative_results(wire),
            wire=wire,
            content_type=response.headers.get("content-type", "application/octet-stream"),
            url=election_url,
        )
