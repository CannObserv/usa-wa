"""SOSAdapter — the WA Secretary of State votewa source adapter (Layer 3), archive-only (#100).

Fetches a general election's candidate-filing cohort from the votewa CSV export —
``sos-whofiled:<YYYYMM>`` — and archives the pristine CSV (#54). It does **not** normalize:
the SOS contribution (the House ``Position 1/2`` qualifier that PDC lacks pre-2018) is derived
**archive-first** by the Phase B position provider
(:mod:`usa_wa_adapter_sos.filings.cohort`), joined to the PDC winner cohort by
``(LD, surname, party)``. The historical harvest drives this adapter
through :meth:`~clearinghouse_core.runner.AdapterRunner.archive_only`; :meth:`normalize` is
therefore unused and raises — symmetric with :class:`~usa_wa_adapter_pdc.adapter.PDCAdapter`.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime

from clearinghouse_core.adapter import BaseAdapter, FetchedPayload, NormalizedBatch, ResourceRef
from usa_wa_adapter_sos.filings.transport import (
    SOS_BASE_URL,
    WHOFILED_EXPORT_PATH,
    SOSFilingsClient,
    general_election_date,
)

#: ``fetch_one`` resource-id prefix for a general-election candidate-filing cohort.
WHOFILED_RESOURCE_PREFIX = "sos-whofiled:"

#: The real votewa endpoint the bytes came from (#54 provenance). ``fetch_one`` stamps
#: ``FetchEvent.url`` as ``{endpoint}?{query}#{resource_id}`` — derived from module constants so
#: URL provenance is independent of the (possibly faked) client.
_EXPORT_ENDPOINT = f"{SOS_BASE_URL}{WHOFILED_EXPORT_PATH}"


def whofiled_resource_id(election_year: int) -> str:
    """The archive resource id for a general election's filings — ``sos-whofiled:<YYYYMM>``."""
    return f"{WHOFILED_RESOURCE_PREFIX}{general_election_date(election_year)}"


def election_year_from_resource_id(resource_id: str) -> int:
    """Recover the election year from a ``sos-whofiled:<YYYYMM>`` resource id."""
    if not resource_id.startswith(WHOFILED_RESOURCE_PREFIX):
        raise ValueError(f"unknown resource_id: {resource_id!r}")
    return int(resource_id[len(WHOFILED_RESOURCE_PREFIX) : len(WHOFILED_RESOURCE_PREFIX) + 4])


def _query(election_year: int) -> str:
    """The export query string stamped onto ``FetchEvent.url`` (provenance of the pull)."""
    params = SOSFilingsClient.whofiled_params(general_election_date(election_year))
    return "&".join(f"{k}={v}" for k, v in params.items())


class SOSAdapter(BaseAdapter):
    """WA Secretary of State votewa source adapter (Layer 3), archive-only (#100)."""

    source_slug = "usa_wa_sos"
    schema_name = "usa_wa_sos"
    jurisdiction_slug = "usa-wa"

    def __init__(
        self, *, election_years: list[int], client: SOSFilingsClient | None = None
    ) -> None:
        self.election_years = election_years
        self._client = client or SOSFilingsClient()

    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        """Yield a filing cohort per configured election year. Callers archive these via
        ``archive_only``; the Phase B provider derives the House position from the archive."""
        for year in self.election_years:
            yield ResourceRef(resource_id=whofiled_resource_id(year))

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Fetch one filing cohort, archiving the pristine CSV body (#54). The resource id is
        stamped into ``FetchEvent.url`` as a fragment (provenance of which cohort was pulled)."""
        election_year = election_year_from_resource_id(resource_id)
        fetched = await self._client.fetch_whofiled(election_year)
        return FetchedPayload(
            url=f"{_EXPORT_ENDPOINT}?{_query(election_year)}#{resource_id}",
            fetched_at=datetime.now(UTC),
            content_type=fetched.content_type,
            body=fetched.wire,
            http_status=200,
            parsed=fetched.records,
        )

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        """Unused — SOS is archive-only (#100). The House position qualifier is joined to the
        PDC winner cohort by :mod:`usa_wa_adapter_sos.filings.cohort`, not emitted here. Drive this
        adapter through ``AdapterRunner.archive_only``, not ``fetch_and_normalize``."""
        raise NotImplementedError(
            "SOSAdapter is archive-only (#100); derive positions via sos_cohort, not normalize"
        )
