"""WA State Legislature SOAP adapter — P1a first cut.

Today's scope (P1a): one resource — ``committees:<biennium>`` — fetched once
per refresh from ``CommitteeService.GetActiveCommittees`` and normalized to
canonical Organization rows under the appropriate chamber Org parent.

Bills, sessions discovery, member rosters, vote events, hearings, etc.
remain stubbed for subsequent P1 cuts.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable
from datetime import UTC, datetime

from ulid import ULID as _ULID

from clearinghouse_core.adapter import (
    BaseAdapter,
    FetchedPayload,
    NormalizedBatch,
    ResourceRef,
)
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.committees import normalize_committees
from usa_wa_adapter_legislature.transport import WSL_BASE_URL, WSLClient

COMMITTEES_RESOURCE_PREFIX = "committees:"


class WALegislatureAdapter(BaseAdapter):
    """WA State Legislature SOAP source adapter (Layer 3, P1a scope)."""

    source_slug = "usa_wa_legislature"
    schema_name = "usa_wa_legislature"
    jurisdiction_slug = "usa-wa"

    def __init__(
        self,
        *,
        anchors: BootstrapAnchors,
        jurisdiction_id: _ULID,
        biennium: str,
        client: WSLClient | None = None,
    ) -> None:
        self.anchors = anchors
        self.jurisdiction_id = jurisdiction_id
        self.biennium = biennium
        self._committee_client = client or WSLClient("CommitteeService")

    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        """Yield one ref for the committees resource (P1a single-pull design)."""
        yield ResourceRef(resource_id=f"{COMMITTEES_RESOURCE_PREFIX}{self.biennium}")

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Pull the committee list from WSL and stash as JSON-encoded bytes."""
        if not resource_id.startswith(COMMITTEES_RESOURCE_PREFIX):
            raise ValueError(f"unknown resource_id: {resource_id!r}")
        committees = self._committee_client.get_active_committees()
        body = json.dumps(committees).encode("utf-8")
        return FetchedPayload(
            url=f"{WSL_BASE_URL}/CommitteeService.asmx#GetActiveCommittees",
            fetched_at=datetime.now(UTC),
            content_type="application/json",
            body=body,
            http_status=200,
        )

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        return await normalize_committees(
            payload,
            anchors=self.anchors,
            jurisdiction_id=self.jurisdiction_id,
        )
