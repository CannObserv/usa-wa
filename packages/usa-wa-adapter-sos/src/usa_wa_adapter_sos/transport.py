"""Transport — ``httpx`` client for the WA Secretary of State ``votewa`` filing export.

The SOS publishes approved candidate filings per election at ``eledataweb.votewa.gov``. The
``WhoFiled`` page's *Export To Excel* control resolves to ``/Candidates/ExportToExcel``, which —
despite the name — serves **CSV** (``text/csv``), one row per candidate, carrying the fields
#100 needs: ``RaceName`` (``State Representative Pos. 1`` / ``Pos. 2``), ``RaceJurisdictionName``
(``Legislative District N``), ``BallotName``, and ``PartyName`` — plus the contact/candidacy
detail #99 will use (``Email`` / ``MailingAddress`` / ``Phone`` / ``FilingDate`` / ``IsWithdrawn``).

Like the PDC SODA transport, this mirrors the :class:`WireFetch` contract — the pristine CSV
body is archived + hashed (#54); the derived parse is a convenience so Phase B doesn't re-decode.

A central courtesy min-interval gate (:data:`_SOS_LIMITER`, the #77 pattern) spaces calls to the
single votewa host regardless of caller; ``USA_WA_SOS_MIN_REQUEST_INTERVAL`` tunes it (default
1.0s, ``0`` disables). votewa is a plain government ASP.NET site with no published API contract,
so the archived wire is the durable record and the endpoint shape is pinned by a cassette test.
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

#: The votewa election-data host.
SOS_BASE_URL = "https://eledataweb.votewa.gov"

#: The candidate-filing CSV export path (the ``WhoFiled`` page's *Export To Excel* control).
WHOFILED_EXPORT_PATH = "/Candidates/ExportToExcel"

#: The ``countyCode`` value selecting *all* counties (statewide) — the legislative races we key
#: on are district-scoped, so a statewide pull is the single call per election.
ALL_COUNTIES = "xx"

#: Default courtesy floor (seconds) between any two votewa calls; overridable via
#: ``USA_WA_SOS_MIN_REQUEST_INTERVAL`` or :func:`configure_sos_rate_limit`. 1.0s is deliberately
#: gentle — votewa is a low-QPS government site and the harvest is a handful of calls.
DEFAULT_SOS_MIN_REQUEST_INTERVAL = 1.0


@dataclass(frozen=True)
class WireFetch:
    """An archival fetch result: the pristine CSV wire bytes plus the derived row parse.

    ``wire`` is the raw CSV response body votewa sent — the provenance source of truth that gets
    archived and hashed (#54). ``records`` is the decoded list of row dicts. Treat ``records`` as
    derivative: if the two ever disagree, ``wire`` is authoritative.
    """

    records: list[dict[str, Any]]
    wire: bytes
    content_type: str


def parse_whofiled(wire: bytes) -> list[dict[str, Any]]:
    """Decode an **archived** votewa filing-export CSV body offline back into row dicts.

    The #56 cache path: re-derive the parse from stored ``RawPayload`` bytes without a re-pull.
    Decodes UTF-8 (BOM-tolerant) and reads the header row as dict keys.
    """
    text = wire.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


class _AsyncRateLimiter:
    """Async min-interval gate. :meth:`acquire` reserves the next evenly-spaced slot under a
    lock, then sleeps (outside the lock) until it, so sequential callers are spaced by
    ``min_interval``. ``monotonic``/``sleep`` are injectable for deterministic tests."""

    def __init__(
        self,
        min_interval: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._min = max(0.0, min_interval)
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next = 0.0

    def set_interval(self, min_interval: float) -> None:
        self._min = max(0.0, min_interval)

    async def acquire(self) -> None:
        if self._min <= 0:
            return
        async with self._lock:
            slot = max(self._monotonic(), self._next)
            self._next = slot + self._min
            delay = slot - self._monotonic()
        if delay > 0:
            await self._sleep(delay)


def _env_min_interval() -> float:
    """Read ``USA_WA_SOS_MIN_REQUEST_INTERVAL``, falling back to the default on a malformed
    value — a bad env var must not crash every votewa caller with an import-time ValueError."""
    raw = os.environ.get("USA_WA_SOS_MIN_REQUEST_INTERVAL")
    if raw is None:
        return DEFAULT_SOS_MIN_REQUEST_INTERVAL
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_SOS_MIN_REQUEST_INTERVAL


#: The one shared limiter every votewa GET passes through (the #77 central-governor pattern).
_SOS_LIMITER = _AsyncRateLimiter(_env_min_interval())


def configure_sos_rate_limit(min_interval: float) -> None:
    """Set the central votewa min-interval (seconds). Maps a harvest's ``--pause-seconds`` onto
    the shared gate; the test suite zeroes it via an autouse fixture."""
    _SOS_LIMITER.set_interval(min_interval)


def general_election_date(election_year: int) -> str:
    """The ``electionDate`` token for a year's **general** election — ``YYYYMM`` with the WA
    general in November. ``2016`` → ``"201611"``."""
    return f"{election_year}11"


class SOSClient:
    """Thin async votewa reader for the candidate-filing CSV export."""

    def __init__(
        self,
        *,
        base_url: str = SOS_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def export_url(self) -> str:
        """The candidate-filing CSV export URL (election is a query param, not a path)."""
        return f"{self._base_url}{WHOFILED_EXPORT_PATH}"

    @staticmethod
    def whofiled_params(election_date: str) -> dict[str, str]:
        """Query params selecting one election's statewide candidate filings."""
        return {"electionDate": election_date, "countyCode": ALL_COUNTIES}

    async def fetch_whofiled(self, election_year: int) -> WireFetch:
        """GET one **general**-election candidate-filing cohort (archived + hashed, #54).

        Passes the central courtesy gate first. Raises ``httpx.HTTPStatusError`` on a non-2xx."""
        await _SOS_LIMITER.acquire()
        params = self.whofiled_params(general_election_date(election_year))
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(self.export_url(), params=params)
            response.raise_for_status()
            wire = response.content
            content_type = response.headers.get("content-type", "text/csv")
        return WireFetch(records=parse_whofiled(wire), wire=wire, content_type=content_type)
