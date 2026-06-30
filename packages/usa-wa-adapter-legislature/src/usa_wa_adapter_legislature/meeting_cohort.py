"""Meeting-derived Joint/`Other` committee cohort for rename detection (#56).

`CommitteeService.GetCommittees` is structurally blind to Joint/`Other` committees (#39),
so #46's roster-diff rename detector never sees their renames. The only source is each
meeting's nested committee refs. This module turns a biennium label into the
``{source_id: name}`` cohort the #56 detector diffs against its predecessor — the exact
shape #46 feeds its shared reconcile spine, just sourced from the meeting docket.

The cohort name is the **clean ``Name``** (mirroring #61's ``observed_name``): the class
stores WSL's agency-double-prefixed ``LongName`` ("Joint Joint …") as ``Organization.name``
but emits the clean ``short_name`` to PM. Diffing and emitting that same clean string keeps
detection and the dated-name evidence consistent (and avoids a double-prefix false-positive
on PM canonicalisation). ``LongName`` is the fallback only when ``Name`` is blank.

Dedup-by-stable-``Id`` and the House/Senate skip are reused from
:func:`~usa_wa_adapter_legislature.normalize.committee_meetings.joint_other_refs`, so the
parse rule lives in one place.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from usa_wa_adapter_legislature.meeting_windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.normalize.committee_meetings import joint_other_refs
from usa_wa_adapter_legislature.normalize.fields import clean_field

logger = get_logger(__name__)


def cohort_name(ref: dict[str, Any]) -> str | None:
    """The clean name PM should receive for one meeting committee ref (#61 ``observed_name``).

    The clean ``Name`` ("Joint Transportation Committee") — **only**. Deliberately *not* the
    agency-double-prefixed ``LongName`` ("Joint Joint …"): the cohort value is both diffed and
    emitted, so a ``LongName`` fallback would send the double-prefixed form to PM, the exact
    thing #61 exists to prevent. ``None`` when ``Name`` is blank — a ref with no clean name
    can't seed a clean rename and is dropped (this has never been observed in WSL data; every
    one of the 55 produced bodies carries a populated ``Name``)."""
    return clean_field(ref.get("Name"))


def meeting_cohort_names(meetings: list[dict[str, Any]]) -> dict[str, str]:
    """Build ``{source_id: clean name}`` for the Joint/`Other` cohort in a window's meetings.

    Reuses :func:`joint_other_refs` for the Agency filter + stable-``Id`` dedup, then keeps
    the clean :func:`cohort_name`. Refs with no usable name are dropped (logged) — they can't
    participate in a name diff."""
    cohort: dict[str, str] = {}
    for source_id, ref in joint_other_refs(meetings).items():
        name = cohort_name(ref)
        if name is None:
            logger.warning("meeting_cohort_ref_unnamed", extra={"source_id": source_id})
            continue
        cohort[source_id] = name
    return cohort


class _MeetingClient(Protocol):
    """The slice of :class:`~usa_wa_adapter_legislature.transport.WSLClient` this needs."""

    async def fetch_committee_meetings(self, begin: Any, end: Any) -> Any: ...

    async def parse_committee_meetings(self, wire: bytes) -> list[dict[str, Any]]: ...


class MeetingCohortProvider:
    """Biennium → meeting-derived ``{source_id: clean name}`` cohort over a WSL client.

    Maps a biennium to its full two-year window (:func:`biennium_window`), obtains the docket,
    and reduces it through :func:`meeting_cohort_names`. The #56 reconcile builds the current
    and prior cohorts through this seam, keeping the WSL/window/parse details out of the diff.

    **Archive-first, read-only.** When given a ``session`` + provenance ``source_id``, the
    provider reads the latest **archived** wire for the window (:class:`RawPayload`, written by
    the daily refresh / #39 harvest) and re-parses it offline via
    :meth:`~usa_wa_adapter_legislature.transport.WSLClient.parse_committee_meetings` — so a
    closed window (immutable, ~1.5 MB) is never re-pulled from WSL on the weekly reconcile.
    Only a window with no archived copy falls back to a live pull (left **un-archived** — the
    reconcile stays read-only, exactly as #46's ``GetCommittees`` reads do; archival belongs to
    refresh/harvest). Constructed with no ``session`` (e.g. a dry preview off-box) it always
    pulls live. No TTL: closed windows don't change, and within a biennium a body's *name* is
    stable, so the latest archived current-window copy is fresh enough for rename detection."""

    def __init__(
        self,
        client: _MeetingClient,
        *,
        session: AsyncSession | None = None,
        source_id: _ULID | None = None,
    ) -> None:
        self._client = client
        self._session = session
        self._source_id = source_id

    async def cohort(self, biennium: str) -> dict[str, str]:
        begin, end = biennium_window(biennium)
        resource_id = meetings_resource_id(begin, end)
        wire = await self._archived_wire(resource_id)
        if wire is not None:
            logger.info("meeting_cohort_cache_hit", extra={"resource_id": resource_id})
            return meeting_cohort_names(await self._client.parse_committee_meetings(wire))
        logger.info("meeting_cohort_live_pull", extra={"resource_id": resource_id})
        fetch = await self._client.fetch_committee_meetings(begin, end)
        return meeting_cohort_names(fetch.records)

    async def _archived_wire(self, resource_id: str) -> bytes | None:
        """The most recent successfully-archived wire for ``resource_id``, or ``None``.

        ``None`` whenever caching is unavailable (no session/source) or the window was never
        archived — the caller then pulls live."""
        if self._session is None or self._source_id is None:
            return None
        stmt = (
            select(RawPayload.body)
            .join(FetchEvent, FetchEvent.id == RawPayload.fetch_event_id)
            .where(
                FetchEvent.source_id == self._source_id,
                FetchEvent.resource_id == resource_id,
                FetchEvent.status == FetchStatus.ok,
            )
            .order_by(FetchEvent.fetched_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
