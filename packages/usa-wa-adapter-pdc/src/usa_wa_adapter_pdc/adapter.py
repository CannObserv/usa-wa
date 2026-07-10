"""PDCAdapter — the WA PDC source adapter (Layer 3), archive-only (#79).

Fetches the two seated-winner cohorts from the PDC ``Campaign Finance Summary`` SODA dataset —
``house-winners:<election_year>`` and ``senate-winners:<election_year>`` — and archives the
pristine JSON (#54). It does **not** normalize: PDC's contribution (House Position seat spans +
``person_wa_pdc`` identifiers) is derived **archive-first** by the Phase B span builder
(:mod:`usa_wa_adapter_pdc.build_pdc_spans`), because a winner→seat match needs the roster of the
biennium the cohort *seated* — the era match (#79) that fixes the #75 current-snapshot
limitation. The daily refresh and the historical harvest both drive this adapter through
:meth:`~clearinghouse_core.runner.AdapterRunner.archive_only`; :meth:`normalize` is therefore
unused and raises.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime

from clearinghouse_core.adapter import BaseAdapter, FetchedPayload, NormalizedBatch, ResourceRef
from usa_wa_adapter_legislature.synthesis import parse_biennium
from usa_wa_adapter_pdc.transport import (
    CAMPAIGN_FINANCE_SUMMARY_RESOURCE,
    PDC_BASE_URL,
    PDCClient,
)

#: ``fetch_one`` resource-id prefix for the seated House winner cohort.
HOUSE_WINNERS_RESOURCE_PREFIX = "house-winners:"

#: ``fetch_one`` resource-id prefix for a seated Senate winner cohort (#75).
SENATE_WINNERS_RESOURCE_PREFIX = "senate-winners:"

#: The real SODA endpoint the bytes came from (#54 provenance). ``fetch_one`` stamps
#: ``FetchEvent.url`` as ``{endpoint}#{resource_id}`` — the office is a query filter, so the
#: endpoint itself is chamber-agnostic; the fragment records which cohort was pulled.
_WINNERS_ENDPOINT = f"{PDC_BASE_URL}/resource/{CAMPAIGN_FINANCE_SUMMARY_RESOURCE}.json"


def _stamp_url(resource_id: str) -> str:
    """Stamp a resource id onto the SODA endpoint as a fragment for ``FetchEvent.url``."""
    return f"{_WINNERS_ENDPOINT}#{resource_id}"


def election_year_for_biennium(biennium: str) -> int:
    """The general-election year that seated a biennium's House — its odd start year
    minus one (WA House is entirely up every even November). ``2025-26`` → ``2024``."""
    start_year, _ = parse_biennium(biennium)
    return start_year - 1


def seating_biennium_for_election_year(election_year: int) -> str:
    """The biennium an even-year general election seats — the inverse of
    :func:`election_year_for_biennium`. ``2012`` → ``"2013-14"`` (a WA biennium starts the odd
    year after the election). Used by the #79 backfill to era-match each cohort to the roster
    it seated, fixing the #75 current-snapshot limitation."""
    start = election_year + 1
    return f"{start}-{(start + 1) % 100:02d}"


def senate_election_years_for_biennium(biennium: str) -> tuple[int, int]:
    """The two general-election years whose winners sit in a biennium's Senate (#75).

    WA Senate is staggered 4-year terms — only ~half the chamber is up each even November —
    so identifying *all* sitting senators requires the union of the two most-recent even
    years: ``start-1`` (seats up that cycle) and ``start-3`` (seats still mid-term). For
    ``2025-26``: ``(2024, 2022)``."""
    start_year, _ = parse_biennium(biennium)
    return (start_year - 1, start_year - 3)


class PDCAdapter(BaseAdapter):
    """WA Public Disclosure Commission SODA source adapter (Layer 3), archive-only (#79)."""

    source_slug = "usa_wa_pdc"
    schema_name = "usa_wa_pdc"
    jurisdiction_slug = "usa-wa"

    def __init__(self, *, biennium: str, client: PDCClient | None = None) -> None:
        self.biennium = biennium
        self.election_year = election_year_for_biennium(biennium)
        self.senate_election_years = senate_election_years_for_biennium(biennium)
        self._client = client or PDCClient()

    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        """Yield the current biennium's House cohort + both staggered Senate cohorts. Callers
        archive these via ``archive_only``; the Phase B builder derives seats from the archive."""
        yield ResourceRef(resource_id=f"{HOUSE_WINNERS_RESOURCE_PREFIX}{self.election_year}")
        for year in self.senate_election_years:
            yield ResourceRef(resource_id=f"{SENATE_WINNERS_RESOURCE_PREFIX}{year}")

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Fetch one cohort, archiving the pristine SODA JSON body (#54). The resource id is
        stamped into ``FetchEvent.url`` as a fragment (provenance of which cohort was pulled)."""
        if resource_id.startswith(HOUSE_WINNERS_RESOURCE_PREFIX):
            election_year = int(resource_id[len(HOUSE_WINNERS_RESOURCE_PREFIX) :])
            fetched = await self._client.fetch_house_winners(election_year)
        elif resource_id.startswith(SENATE_WINNERS_RESOURCE_PREFIX):
            election_year = int(resource_id[len(SENATE_WINNERS_RESOURCE_PREFIX) :])
            fetched = await self._client.fetch_senate_winners(election_year)
        else:
            raise ValueError(f"unknown resource_id: {resource_id!r}")
        return FetchedPayload(
            url=_stamp_url(resource_id),
            fetched_at=datetime.now(UTC),
            content_type=fetched.content_type,
            body=fetched.wire,
            http_status=200,
            parsed=fetched.records,
        )

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        """Unused — PDC is archive-only (#79). The winner→seat derivation is cross-year (a
        merged span) and era-matched, which a single-cohort ``normalize`` cannot express; it is
        done by :func:`usa_wa_adapter_pdc.build_pdc_spans.build_pdc_spans` reading the archive.
        Drive this adapter through ``AdapterRunner.archive_only``, not ``fetch_and_normalize``."""
        raise NotImplementedError(
            "PDCAdapter is archive-only (#79); build seats via build_pdc_spans, not normalize"
        )
