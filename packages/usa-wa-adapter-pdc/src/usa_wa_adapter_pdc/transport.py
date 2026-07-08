"""Transport — ``httpx`` client for the WA PDC Socrata Open Data API (SODA).

The PDC publishes campaign-finance data on data.wa.gov (Socrata). This adapter reads
one dataset — ``Campaign Finance Summary`` (resource ``3h9x-7bvm``) — which carries, per
candidacy, the seated-office fields #69 needs: ``office`` / ``position`` /
``legislative_district`` / ``party_code`` / ``general_election_status`` plus the stable
PDC ``person_id``.

Unlike the WSL SOAP transport (synchronous zeep behind ``asyncio.to_thread``), SODA is
plain REST/JSON, so this client is natively async over ``httpx``. It mirrors the WSL
:class:`WireFetch` contract — the pristine response body is archived + hashed (#54); the
derived parse is a convenience so the normalizer doesn't re-decode.

An optional application token (``USA_WA_PDC_APP_TOKEN`` → ``X-App-Token`` header) raises
Socrata's per-IP rate limit. It is **not** authentication and **not** required: the
dataset is public and a token only moves throttling from per-IP to per-app. Sent only
when set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

#: data.wa.gov SODA host.
PDC_BASE_URL = "https://data.wa.gov"

#: The ``Campaign Finance Summary`` dataset resource id (one row per candidacy).
CAMPAIGN_FINANCE_SUMMARY_RESOURCE = "3h9x-7bvm"

#: SODA ``office`` value for a WA House seat.
OFFICE_STATE_REPRESENTATIVE = "STATE REPRESENTATIVE"

#: SODA ``office`` value for a WA Senate seat (#75).
OFFICE_STATE_SENATOR = "STATE SENATOR"

#: SODA ``general_election_status`` value marking the seated winner — the filter that
#: collapses the many-candidates-per-race rows to the one person per ``(LD, position)``.
WON_IN_GENERAL = "Won in general"


@dataclass(frozen=True)
class WireFetch:
    """An archival fetch result: the pristine wire bytes plus the derived parse.

    ``wire`` is the raw JSON response body Socrata sent — the provenance source of truth
    that gets archived and hashed (#54). ``records`` is the decoded list of row dicts.
    Treat ``records`` as derivative: if the two ever disagree, ``wire`` is authoritative.
    """

    records: list[dict[str, Any]]
    wire: bytes
    content_type: str


def _parse_winner_rows(wire: bytes) -> list[dict[str, Any]]:
    """Decode a SODA response body (a top-level JSON array of row objects) offline.

    The #56 cache path, shared by the House and Senate re-parsers: re-derive the parse
    from stored ``RawPayload`` bytes without a re-pull.
    """
    decoded = json.loads(wire.decode("utf-8"))
    if not isinstance(decoded, list):
        raise ValueError(f"expected a JSON array of rows, got {type(decoded).__name__}")
    return decoded


def parse_house_winners(wire: bytes) -> list[dict[str, Any]]:
    """Decode an **archived** seated-House SODA response body offline back into rows."""
    return _parse_winner_rows(wire)


def parse_senate_winners(wire: bytes) -> list[dict[str, Any]]:
    """Decode an **archived** seated-Senate SODA response body offline back into rows (#75)."""
    return _parse_winner_rows(wire)


class PDCClient:
    """Thin async SODA reader for the PDC campaign-finance dataset."""

    def __init__(
        self,
        *,
        base_url: str = PDC_BASE_URL,
        app_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._app_token = app_token
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._app_token:
            headers["X-App-Token"] = self._app_token
        return headers

    def winners_url(self) -> str:
        """The SODA resource URL for the campaign-finance dataset (JSON) — office-agnostic
        (the chamber is a query filter, not a path)."""
        return f"{self._base_url}/resource/{CAMPAIGN_FINANCE_SUMMARY_RESOURCE}.json"

    @staticmethod
    def _winners_params(office: str, election_year: int) -> dict[str, str]:
        """SoQL query params selecting the seated winners of one ``office`` for one election
        year — ``general_election_status = 'Won in general'`` collapses the
        many-candidates-per-race rows to the one seated winner per seat."""
        return {
            "office": office,
            "election_year": str(election_year),
            "$where": f"general_election_status='{WON_IN_GENERAL}'",
            "$limit": "5000",
        }

    @staticmethod
    def house_winners_params(election_year: int) -> dict[str, str]:
        """SoQL params selecting the seated House winners (one per ``(LD, position)``)."""
        return PDCClient._winners_params(OFFICE_STATE_REPRESENTATIVE, election_year)

    @staticmethod
    def senate_winners_params(election_year: int) -> dict[str, str]:
        """SoQL params selecting the seated Senate winners (one per LD) for the year (#75)."""
        return PDCClient._winners_params(OFFICE_STATE_SENATOR, election_year)

    async def _fetch_winners(self, params: dict[str, str]) -> WireFetch:
        """GET a seated-winner cohort for the given SoQL ``params``, archiving the pristine
        JSON body (#54). Raises ``httpx.HTTPStatusError`` on a non-2xx response."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(self.winners_url(), params=params, headers=self._headers())
            response.raise_for_status()
            wire = response.content
            content_type = response.headers.get("content-type", "application/json")
        return WireFetch(records=_parse_winner_rows(wire), wire=wire, content_type=content_type)

    async def fetch_house_winners(self, election_year: int) -> WireFetch:
        """GET the seated House winner cohort for ``election_year`` (archived + hashed)."""
        return await self._fetch_winners(self.house_winners_params(election_year))

    async def fetch_senate_winners(self, election_year: int) -> WireFetch:
        """GET the seated Senate winner cohort for ``election_year`` (archived + hashed, #75)."""
        return await self._fetch_winners(self.senate_winners_params(election_year))
