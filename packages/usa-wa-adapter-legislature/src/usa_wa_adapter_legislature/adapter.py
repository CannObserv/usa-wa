"""WA State Legislature SOAP adapter.

Resources today:

- ``committees:<biennium>`` — the House/Senate standing committees from
  ``CommitteeService.GetActiveCommittees`` (P1a).
- ``committees-roster:<biennium>`` — the full historical roster from
  ``CommitteeService.GetCommittees(biennium)``, the sub-project-3 backfill archive
  (a distinct SOAP op from GetActiveCommittees). Normalizes through the same
  Committee shape.
- ``committee-meetings:<begin>:<end>`` — the meeting docket window from
  ``CommitteeMeetingService.GetCommitteeMeetings``, the only source of the
  Joint/`Other` committee class (#39). Driven per date window by the backfill CLI
  and (current window) the daily refresh.
- ``sponsors:<biennium>`` — the member roster from ``SponsorService.GetSponsors``
  (P1b): Person + identifier only (party/Senate-seat tenure are archive-derived
  merged spans, Phase B / #78-2c, not per-biennium here).
- ``committee-members-hist:<biennium>:<id>:<agency>:<name>`` — one committee's roster
  for a biennium from ``CommitteeService.GetCommitteeMembers`` (#82): Person cluster
  only. Membership tenure is an archive-derived merged span, and the daily refresh keys
  this same op by the current biennium (it returns exactly the GetActiveCommitteeMembers
  set), so one uniform archive covers current + history.

All archive the pristine SOAP wire as ``RawPayload.body`` (#54) and carry the
zeep-derived dicts on ``FetchedPayload.parsed`` so the normalizer skips a re-parse.
The sponsor/committee-member normalizers additionally need the runner's session
(intra-batch FK resolution); pass ``WALegislatureAdapter(session=...)``. Bills, vote
events, etc. remain stubbed for later cuts.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
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
from usa_wa_adapter_legislature.normalize.members import normalize_member_persons
from usa_wa_adapter_legislature.normalize.sponsors import normalize_sponsors
from usa_wa_adapter_legislature.transport import WSL_BASE_URL, WSLClient

COMMITTEES_RESOURCE_PREFIX = "committees:"
#: Historical full-roster archive (sub-project 3), distinct from the daily
#: ``committees:<biennium>`` GetActiveCommittees archive — a *different* SOAP
#: operation (``GetCommittees(biennium)`` full roster vs GetActiveCommittees'
#: implicit-current active set), so the wire genuinely differs and the two keys
#: never collide. Phase B's rename-chain reads only this key.
COMMITTEES_ROSTER_RESOURCE_PREFIX = "committees-roster:"
#: The member cluster (P1b). ``sponsors:<biennium>`` drives GetSponsors;
#: ``committee-members:<committee_id>:<agency>:<name>`` drives one committee's
#: GetActiveCommitteeMembers roster (the committee id rides the resource id so the
#: normalizer can resolve the committee Org — the members payload doesn't carry it).
SPONSORS_RESOURCE_PREFIX = "sponsors:"
#: The **historical** roster key (#82): ``committee-members-hist:<biennium>:<id>:<agency>:<name>``
#: drives ``GetCommitteeMembers(biennium, agency, name)`` — a distinct SOAP op (and so a
#: distinct provenance key) from the biennium-less, current-only ``committee-members:`` above.
#: The biennium leads so a committee's rosters sort by era; the id lets the span builder key
#: citations per (biennium, committee).
COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX = "committee-members-hist:"

_COMMITTEE_SERVICE_URL = f"{WSL_BASE_URL}/CommitteeService.asmx"
_COMMITTEES_URL = f"{_COMMITTEE_SERVICE_URL}#GetActiveCommittees"
_COMMITTEES_ROSTER_URL = f"{_COMMITTEE_SERVICE_URL}#GetCommittees"
_MEETINGS_URL = f"{WSL_BASE_URL}/CommitteeMeetingService.asmx#GetCommitteeMeetings"
_SPONSORS_URL = f"{WSL_BASE_URL}/SponsorService.asmx#GetSponsors"
#: The #82 roster fragment. The biennium + committee id ride a ``?…=`` query stamped
#: **before** the fragment so the stamped url stays well-formed (query precedes fragment);
#: ``normalize`` dispatches on this fragment suffix.
_COMMITTEE_MEMBERS_HIST_FRAGMENT = "#GetCommitteeMembers"


def committee_members_hist_resource_id(
    biennium: str, committee_source_id: str, agency: str, committee_name: str
) -> str:
    """Build the ``committee-members-hist:<biennium>:<id>:<agency>:<name>`` resource id (#82)."""
    return (
        f"{COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX}{biennium}:{committee_source_id}:"
        f"{agency}:{committee_name}"
    )


def parse_committee_members_hist_resource_id(resource_id: str) -> tuple[str, str, str, str]:
    """Parse ``committee-members-hist:<biennium>:<id>:<agency>:<name>`` →
    (biennium, committee_id, agency, name).

    Splits on the first three colons only, so a committee ``Name`` containing a colon
    (none do today) still round-trips in the trailing segment."""
    rest = resource_id[len(COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX) :]
    biennium, committee_source_id, agency, committee_name = rest.split(":", 3)
    return biennium, committee_source_id, agency, committee_name


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
        sponsor_client: WSLClient | None = None,
        member_client: WSLClient | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.anchors = anchors
        self.jurisdiction_id = jurisdiction_id
        self.biennium = biennium
        self._committee_client = client or WSLClient("CommitteeService")
        self._meeting_client = meeting_client or WSLClient("CommitteeMeetingService")
        self._sponsor_client = sponsor_client or WSLClient("SponsorService")
        # GetActiveCommitteeMembers is a CommitteeService op; it defaults to the same
        # client as the committees pull, but is a distinct injection point so tests can
        # fake the member fan-out without disturbing the (cassette-backed) committees pull.
        self._member_client = member_client or self._committee_client
        # The member normalizers (sponsors / committee-members) resolve Person/Role ids
        # against the DB to wire Assignments, so they need the runner's session. The
        # committee/meeting normalizers don't — session stays optional for them.
        self._session = session

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "member normalization requires a session; construct "
                "WALegislatureAdapter(session=...)"
            )
        return self._session

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
        if resource_id.startswith(COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX):
            return await self._fetch_historical_committee_members(resource_id)
        if resource_id.startswith(SPONSORS_RESOURCE_PREFIX):
            return await self._fetch_sponsors(resource_id)
        # Roster before the plain committees check: the biennium comes from the
        # resource id (so one adapter can sweep bienniums in the harvest), not
        # self.biennium. The two prefixes don't overlap, but check roster first for
        # clarity.
        if resource_id.startswith(COMMITTEES_ROSTER_RESOURCE_PREFIX):
            return await self._fetch_committees_roster(resource_id)
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

    async def _fetch_committees_roster(self, resource_id: str) -> FetchedPayload:
        """Archive an explicit biennium's full GetCommittees roster (sub-project 3).

        Biennium is parsed from ``committees-roster:<biennium>`` so the harvest sweeps
        many bienniums through one adapter. Stamps ``_COMMITTEES_ROSTER_URL`` (a
        committee normalize target — the ``normalize`` else-branch routes it to
        ``normalize_committees``, the same Committee shape as GetActiveCommittees)."""
        biennium = resource_id[len(COMMITTEES_ROSTER_RESOURCE_PREFIX) :]
        fetched = await self._committee_client.fetch_committees(biennium)
        return FetchedPayload(
            url=_COMMITTEES_ROSTER_URL,
            fetched_at=datetime.now(UTC),
            content_type=fetched.content_type,
            body=fetched.wire,
            http_status=200,
            parsed=fetched.records,
        )

    async def _fetch_sponsors(self, resource_id: str) -> FetchedPayload:
        """Archive a biennium's GetSponsors roster (P1b). Biennium from the resource id."""
        biennium = resource_id[len(SPONSORS_RESOURCE_PREFIX) :]
        fetched = await self._sponsor_client.fetch_sponsors(biennium)
        return FetchedPayload(
            url=_SPONSORS_URL,
            fetched_at=datetime.now(UTC),
            content_type=fetched.content_type,
            body=fetched.wire,
            http_status=200,
            parsed=fetched.records,
        )

    async def _fetch_historical_committee_members(self, resource_id: str) -> FetchedPayload:
        """Archive one committee's roster for a biennium (#82, GetCommitteeMembers).

        The biennium + committee id ride the stamped ``url`` (query before fragment) so the
        archived payload is self-describing; ``normalize`` dispatches on the fragment. A
        committee absent that biennium yields an empty wire (benign Fault swallowed in the
        transport) — the fan-out records the attempt and moves on."""
        biennium, committee_source_id, agency, name = parse_committee_members_hist_resource_id(
            resource_id
        )
        fetched = await self._member_client.fetch_historical_committee_members(
            biennium, agency, name
        )
        return FetchedPayload(
            url=f"{_COMMITTEE_SERVICE_URL}?biennium={biennium}&committee_id={committee_source_id}"
            f"{_COMMITTEE_MEMBERS_HIST_FRAGMENT}",
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
        if payload.url == _SPONSORS_URL:
            return await normalize_sponsors(payload, session=self._require_session())
        # Committee roster (#82) — Persons only: membership tenure is an archive-derived
        # merged span (Phase B), not a per-biennium row.
        if payload.url.endswith(_COMMITTEE_MEMBERS_HIST_FRAGMENT):
            return await normalize_member_persons(payload, session=self._require_session())
        return await normalize_committees(
            payload,
            anchors=self.anchors,
            jurisdiction_id=self.jurisdiction_id,
        )
