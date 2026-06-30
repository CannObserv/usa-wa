"""WA State Legislature SOAP adapter.

Resources today:

- ``committees:<biennium>`` — the House/Senate standing committees from
  ``CommitteeService.GetActiveCommittees`` (P1a).
- ``committee-meetings:<begin>:<end>`` — the meeting docket window from
  ``CommitteeMeetingService.GetCommitteeMeetings``, the only source of the
  Joint/`Other` committee class (#39). Driven per date window by the backfill CLI
  and (current window) the daily refresh.

Both archive the pristine SOAP wire as ``RawPayload.body`` (#54) and carry the
zeep-derived dicts on ``FetchedPayload.parsed`` so the normalizer skips a re-parse.
Bills, member rosters, vote events, etc. remain stubbed for later cuts.
"""

from __future__ import annotations

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
from usa_wa_adapter_legislature.meeting_windows import (
    COMMITTEE_MEETINGS_RESOURCE_PREFIX,
    parse_meetings_resource_id,
)
from usa_wa_adapter_legislature.normalize.committee_meetings import (
    normalize_committee_meetings,
)
from usa_wa_adapter_legislature.normalize.committees import normalize_committees
from usa_wa_adapter_legislature.transport import WSL_BASE_URL, WSLClient

COMMITTEES_RESOURCE_PREFIX = "committees:"

_COMMITTEES_URL = f"{WSL_BASE_URL}/CommitteeService.asmx#GetActiveCommittees"
_MEETINGS_URL = f"{WSL_BASE_URL}/CommitteeMeetingService.asmx#GetCommitteeMeetings"


class WALegislatureAdapter(BaseAdapter):
    """WA State Legislature SOAP source adapter (Layer 3)."""

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
        meeting_client: WSLClient | None = None,
    ) -> None:
        self.anchors = anchors
        self.jurisdiction_id = jurisdiction_id
        self.biennium = biennium
        self._committee_client = client or WSLClient("CommitteeService")
        self._meeting_client = meeting_client or WSLClient("CommitteeMeetingService")

    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        """Yield the committees resource (the meeting docket is driven explicitly).

        The Joint/`Other` meeting-docket windows are fetched by the backfill CLI and
        the daily refresh by constructing their ``committee-meetings:<begin>:<end>``
        ids directly, not via discovery — closed windows must not re-enter the daily
        loop and re-pull immutable history (#39)."""
        yield ResourceRef(resource_id=f"{COMMITTEES_RESOURCE_PREFIX}{self.biennium}")

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Fetch one resource, archiving the pristine SOAP wire as ``body`` (#54)."""
        if resource_id.startswith(COMMITTEE_MEETINGS_RESOURCE_PREFIX):
            return await self._fetch_committee_meetings(resource_id)
        if resource_id.startswith(COMMITTEES_RESOURCE_PREFIX):
            return await self._fetch_committees()
        raise ValueError(f"unknown resource_id: {resource_id!r}")

    async def _fetch_committees(self) -> FetchedPayload:
        fetched = await self._committee_client.fetch_active_committees()
        return FetchedPayload(
            url=_COMMITTEES_URL,
            fetched_at=datetime.now(UTC),
            content_type=fetched.content_type,
            body=fetched.wire,
            http_status=200,
            parsed=fetched.records,
        )

    async def _fetch_committee_meetings(self, resource_id: str) -> FetchedPayload:
        begin, end = parse_meetings_resource_id(resource_id)
        fetched = await self._meeting_client.fetch_committee_meetings(begin, end)
        return FetchedPayload(
            url=_MEETINGS_URL,
            fetched_at=datetime.now(UTC),
            content_type=fetched.content_type,
            body=fetched.wire,
            http_status=200,
            parsed=fetched.records,
        )

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        """Dispatch to the normalizer for the payload's service.

        Keyed on an **exact** match of the URL this adapter's ``fetch_one`` stamped
        (``_MEETINGS_URL`` / ``_COMMITTEES_URL``) — not a substring test — so a future
        resource can't mis-route by coincidence."""
        if payload.url == _MEETINGS_URL:
            return await normalize_committee_meetings(
                payload,
                anchors=self.anchors,
                jurisdiction_id=self.jurisdiction_id,
            )
        return await normalize_committees(
            payload,
            anchors=self.anchors,
            jurisdiction_id=self.jurisdiction_id,
        )
