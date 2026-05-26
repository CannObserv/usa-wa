"""WA Legislature SOAP adapter shell.

P1a will fill in:

- A `zeep` (or raw `httpx` + lxml) SOAP client targeting
  ``https://wslwebservices.leg.wa.gov/``
- ``discover`` — call ``GetCurrentlyActiveBills`` (or similar) for refresh mode
- ``fetch_one`` — call ``GetLegislation`` for an individual bill
- ``normalize`` — parse the SOAP envelope to ``Bill`` + ``BillSponsorship`` +
  ``BillAction`` + ``Legislator`` rows, tagging each with
  ``jurisdiction_id='usa-wa'`` and ``source='usa_wa_legislature'``
"""

from collections.abc import AsyncIterable
from datetime import datetime

from clearinghouse_core.adapter import (
    BaseAdapter,
    FetchedPayload,
    NormalizedBatch,
    ResourceRef,
)


class WALegislatureAdapter(BaseAdapter):
    """WA State Legislature SOAP source adapter (Layer 3, P1a scope)."""

    source_slug = "usa_wa_legislature"
    schema_name = "usa_wa_legislature"
    jurisdiction_slug = "usa-wa"

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        raise NotImplementedError("WALegislatureAdapter.fetch_one — lands in P1a")

    def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        raise NotImplementedError("WALegislatureAdapter.discover — lands in P1a")

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        raise NotImplementedError("WALegislatureAdapter.normalize — lands in P1a")
