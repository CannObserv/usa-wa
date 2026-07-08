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
    SenateEntry,
    normalize_house_positions,
)
from usa_wa_adapter_pdc.normalize.senate_identity import normalize_senate_identities
from usa_wa_adapter_pdc.transport import (
    CAMPAIGN_FINANCE_SUMMARY_RESOURCE,
    PDC_BASE_URL,
    PDCClient,
)

#: ``discover`` / ``fetch_one`` resource-id prefix for the seated House winner cohort.
HOUSE_WINNERS_RESOURCE_PREFIX = "house-winners:"

#: ``discover`` / ``fetch_one`` resource-id prefix for a seated Senate winner cohort (#75).
SENATE_WINNERS_RESOURCE_PREFIX = "senate-winners:"

#: The real SODA endpoint the bytes came from (#54 provenance), not a Python module path.
#: ``fetch_one`` stamps ``FetchEvent.url`` as ``{endpoint}#{resource_id}`` so ``normalize``
#: can route by the stamped URL (the codebase's adapter convention) — the office is a query
#: filter, not a path, so the endpoint itself is chamber-agnostic.
_WINNERS_ENDPOINT = f"{PDC_BASE_URL}/resource/{CAMPAIGN_FINANCE_SUMMARY_RESOURCE}.json"


def _stamp_url(resource_id: str) -> str:
    """Stamp a resource id onto the SODA endpoint as a fragment for ``FetchEvent.url`` —
    the single definition of the fetch↔normalize routing contract (inverse of
    :func:`_resource_of`)."""
    return f"{_WINNERS_ENDPOINT}#{resource_id}"


def _resource_of(payload: FetchedPayload) -> str:
    """Recover the resource id ``fetch_one`` stamped onto ``payload.url`` (inverse of
    :func:`_stamp_url`); ``""`` if no fragment is present."""
    _, _, resource_id = payload.url.partition("#")
    return resource_id


def election_year_for_biennium(biennium: str) -> int:
    """The general-election year that seated a biennium's House — its odd start year
    minus one (WA House is entirely up every even November). ``2025-26`` → ``2024``."""
    start_year, _ = parse_biennium(biennium)
    return start_year - 1


def senate_election_years_for_biennium(biennium: str) -> tuple[int, int]:
    """The two general-election years whose winners sit in a biennium's Senate (#75).

    WA Senate is staggered 4-year terms — only ~half the chamber is up each even November —
    so identifying *all* sitting senators requires the union of the two most-recent even
    years: ``start-1`` (seats up that cycle) and ``start-3`` (seats still mid-term). For
    ``2025-26``: ``(2024, 2022)`` — senators elected 2022 serve 2023-2026, those elected
    2024 serve 2025-2028; both sit during the biennium."""
    start_year, _ = parse_biennium(biennium)
    return (start_year - 1, start_year - 3)


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
        senate_roster: dict[int, list[SenateEntry]] | None = None,
        client: Any | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.anchors = anchors
        self.biennium = biennium
        self.election_year = election_year_for_biennium(biennium)
        self.senate_election_years = senate_election_years_for_biennium(biennium)
        self.house_roster = house_roster
        # Senate roster (from GetSponsors) — the confirming signal + mover cross-link for
        # the #74 mid-biennium replacement inference AND the match target for the #75 Senate
        # identifier cross-link. Empty → no Senate discovery (nothing to match against).
        self.senate_roster = senate_roster or {}
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
        """Yield the seated House winner cohort, plus (when a Senate roster is supplied to
        match against, #75) both staggered Senate cohorts for the biennium."""
        yield ResourceRef(resource_id=f"{HOUSE_WINNERS_RESOURCE_PREFIX}{self.election_year}")
        if self.senate_roster:
            for year in self.senate_election_years:
                yield ResourceRef(resource_id=f"{SENATE_WINNERS_RESOURCE_PREFIX}{year}")

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Fetch one resource, archiving the pristine SODA JSON body (#54). The resource id
        is stamped into ``FetchEvent.url`` as a fragment so ``normalize`` can route on it."""
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
        """Route the archived payload by its stamped resource fragment (symmetric with
        ``fetch_one``): Senate → the identifier-only Senate normalizer (#75), House → the
        House-position normalizer, anything else → ``ValueError`` (no silent House default)."""
        resource_id = _resource_of(payload)
        if resource_id.startswith(SENATE_WINNERS_RESOURCE_PREFIX):
            return await normalize_senate_identities(
                payload,
                senate_roster=self.senate_roster,
                session=self._require_session(),
            )
        if resource_id.startswith(HOUSE_WINNERS_RESOURCE_PREFIX):
            return await normalize_house_positions(
                payload,
                house_roster=self.house_roster,
                anchors=self.anchors,
                session=self._require_session(),
                biennium=self.biennium,
                senate_roster=self.senate_roster,
            )
        raise ValueError(f"cannot route payload for resource {resource_id!r}")
