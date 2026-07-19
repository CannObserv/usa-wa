"""ResultsAdapter — WA SOS ``results.vote.wa.gov`` adapter (Layer 3), archive-only (#101).

Fetches a general election's Legislative **results** CSV (via the ``export.html`` traversal) and
archives the pristine CSV (#54) under ``sos-legresults:<YYYYMMDD>`` — a distinct source
(``usa_wa_sos_results``) and archive key from the filings source, per the multi-source pattern
([`docs/ARCHITECTURE.md`](../../../../docs/ARCHITECTURE.md)). It does **not** normalize: the House
position is a cross-year join to the WSL roster, derived **archive-first** by the Phase B provider
(:mod:`usa_wa_adapter_sos.results.cohort`). Drive it through
:meth:`~clearinghouse_core.runner.AdapterRunner.archive_only`; :meth:`normalize` raises — symmetric
with the filings :class:`~usa_wa_adapter_sos.filings.adapter.SOSAdapter`.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime

from clearinghouse_core.adapter import BaseAdapter, FetchedPayload, NormalizedBatch, ResourceRef
from usa_wa_adapter_sos.results.transport import (
    RESULTS_BASE_URL,
    SOSResultsClient,
    general_election_date,
)

#: ``fetch_one`` resource-id prefix for a general-election Legislative results cohort.
LEGRESULTS_RESOURCE_PREFIX = "sos-legresults:"


def legresults_resource_id(election_year: int) -> str:
    """The archive resource id for a general election's results — ``sos-legresults:<YYYYMMDD>``."""
    return f"{LEGRESULTS_RESOURCE_PREFIX}{general_election_date(election_year)}"


def election_year_from_resource_id(resource_id: str) -> int:
    """Recover the election year from a ``sos-legresults:<YYYYMMDD>`` resource id."""
    if not resource_id.startswith(LEGRESULTS_RESOURCE_PREFIX):
        raise ValueError(f"unknown resource_id: {resource_id!r}")
    return int(resource_id[len(LEGRESULTS_RESOURCE_PREFIX) : len(LEGRESULTS_RESOURCE_PREFIX) + 4])


class ResultsAdapter(BaseAdapter):
    """WA SOS ``results.vote.wa.gov`` source adapter (Layer 3), archive-only (#101)."""

    source_slug = "usa_wa_sos_results"
    schema_name = "usa_wa_sos_results"
    jurisdiction_slug = "usa-wa"

    def __init__(
        self, *, election_years: list[int], client: SOSResultsClient | None = None
    ) -> None:
        self.election_years = election_years
        self._client = client or SOSResultsClient()

    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        """Yield a Legislative-results cohort per configured election year. Callers archive these
        via ``archive_only``; the Phase B provider derives the House position from the archive."""
        for year in self.election_years:
            yield ResourceRef(resource_id=legresults_resource_id(year))

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Fetch one Legislative-results cohort, archiving the pristine CSV body (#54). The
        election's ``export.html`` index (the durable discovery anchor) + the resource id fragment
        are stamped into ``FetchEvent.url`` — derived from module constants, independent of a faked
        client (the actual CSV filename is discovered per-election and varies)."""
        election_year = election_year_from_resource_id(resource_id)
        fetched = await self._client.fetch_legislative_results(election_year)
        election_date = general_election_date(election_year)
        return FetchedPayload(
            url=f"{RESULTS_BASE_URL}/results/{election_date}/export.html#{resource_id}",
            fetched_at=datetime.now(UTC),
            content_type=fetched.content_type,
            body=fetched.wire,
            http_status=200,
            parsed=fetched.records,
        )

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        """Unused — archive-only (#101). The House position is joined to the WSL roster by
        :mod:`usa_wa_adapter_sos.results.cohort`, not emitted here. Drive this adapter through
        ``AdapterRunner.archive_only``, not ``fetch_and_normalize``."""
        raise NotImplementedError(
            "ResultsAdapter is archive-only (#101); derive positions via results.cohort, "
            "not normalize"
        )
