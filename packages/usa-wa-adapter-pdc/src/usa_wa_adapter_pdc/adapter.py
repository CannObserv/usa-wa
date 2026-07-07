"""PDCAdapter — the WA PDC source adapter (Layer 3).

Dispatches the one resource this cut needs: ``house-winners:<election_year>`` — the seated
House winner cohort from the PDC ``Campaign Finance Summary`` SODA dataset — and normalizes
it into ``person_wa_pdc`` identifiers + House seat Assignments (#69).

**Session-aware** (like the WSL member adapter): the normalizer resolves the existing WSL
:class:`Person` and get-or-creates the shared seat Role against the session, so the
Assignment's FKs are real intra-batch. The ``house_roster`` — the WSL House
``(LD, folded-last) → member id`` map used to match a PDC winner to its Person — is built by
the caller from a ``GetSponsors`` pull (House districts aren't stored locally) and injected.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.adapter import BaseAdapter, FetchedPayload, NormalizedBatch, ResourceRef
from usa_wa_adapter_legislature.synthesis import parse_biennium
from usa_wa_adapter_pdc.normalize.house_positions import (
    HouseRosterEntry,
    normalize_house_positions,
)
from usa_wa_adapter_pdc.transport import (
    CAMPAIGN_FINANCE_SUMMARY_RESOURCE,
    PDC_BASE_URL,
    PDCClient,
)

#: ``discover`` / ``fetch_one`` resource-id prefix for the seated House winner cohort.
HOUSE_WINNERS_RESOURCE_PREFIX = "house-winners:"

#: Stamped on ``FetchEvent.url`` — the real SODA endpoint the bytes came from (#54
#: provenance), not a Python module path.
_HOUSE_WINNERS_URL = f"{PDC_BASE_URL}/resource/{CAMPAIGN_FINANCE_SUMMARY_RESOURCE}.json"


def election_year_for_biennium(biennium: str) -> int:
    """The general-election year that seated a biennium's House — its odd start year
    minus one (WA House is entirely up every even November). ``2025-26`` → ``2024``."""
    start_year, _ = parse_biennium(biennium)
    return start_year - 1


class PDCAdapter(BaseAdapter):
    """WA Public Disclosure Commission SODA source adapter (Layer 3)."""

    source_slug = "usa_wa_pdc"
    schema_name = "usa_wa_pdc"
    jurisdiction_slug = "usa-wa"

    def __init__(
        self,
        *,
        anchors: Any,
        biennium: str,
        house_roster: dict[int, list[HouseRosterEntry]],
        client: Any | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.anchors = anchors
        self.biennium = biennium
        self.election_year = election_year_for_biennium(biennium)
        self.house_roster = house_roster
        self._client = client or PDCClient()
        # The House-position normalizer resolves Person/Role ids against the DB to wire
        # Assignments, so it needs the runner's session (mirrors the WSL member adapter).
        self._session = session

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "PDC normalization requires a session; construct PDCAdapter(session=...)"
            )
        return self._session

    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        """Yield the seated House winner cohort for the biennium's election year."""
        yield ResourceRef(resource_id=f"{HOUSE_WINNERS_RESOURCE_PREFIX}{self.election_year}")

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Fetch one resource, archiving the pristine SODA JSON body (#54)."""
        if resource_id.startswith(HOUSE_WINNERS_RESOURCE_PREFIX):
            election_year = int(resource_id[len(HOUSE_WINNERS_RESOURCE_PREFIX) :])
            fetched = await self._client.fetch_house_winners(election_year)
            return FetchedPayload(
                url=_HOUSE_WINNERS_URL,
                fetched_at=datetime.now(UTC),
                content_type=fetched.content_type,
                body=fetched.wire,
                http_status=200,
                parsed=fetched.records,
            )
        raise ValueError(f"unknown resource_id: {resource_id!r}")

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        """Route the archived payload to the House-position normalizer."""
        return await normalize_house_positions(
            payload,
            house_roster=self.house_roster,
            anchors=self.anchors,
            session=self._require_session(),
            biennium=self.biennium,
        )
