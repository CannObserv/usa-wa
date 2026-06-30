"""Committee-meeting fetch windows + resource-id keying (#39).

The meeting docket (`CommitteeMeetingService.GetCommitteeMeetings`) is fetched per
**date window** so each closed window is a stable cache key fetched once — request
frugality, since WSL is a vital upstream we must not hammer. A biennium maps to its
full two-year window; the ``committee-meetings:<begin>:<end>`` resource id keys the
runner's cache-or-fetch decision and the archival ``RawPayload``.

Windows are UTC-naive ``datetime``s because the WSDL parameter is a bare
``s:dateTime`` and zeep serializes naive values without an offset.
"""

from __future__ import annotations

from datetime import date, datetime

from usa_wa_adapter_legislature.synthesis import parse_biennium

COMMITTEE_MEETINGS_RESOURCE_PREFIX = "committee-meetings:"


def biennium_window(biennium: str) -> tuple[datetime, datetime]:
    """Full two-year ``[begin, end]`` window covering a biennium.

    ``2023-24`` → ``(2023-01-01T00:00:00, 2024-12-31T23:59:59)``.
    """
    start, end = parse_biennium(biennium)
    return datetime(start, 1, 1, 0, 0, 0), datetime(end, 12, 31, 23, 59, 59)


def meetings_resource_id(begin: datetime, end: datetime) -> str:
    """Stable cache key for a meetings window: ``committee-meetings:<begin>:<end>``.

    Keyed on the calendar dates (not the times) so a window is one canonical id
    regardless of the sub-day bounds it was built with."""
    return f"{COMMITTEE_MEETINGS_RESOURCE_PREFIX}{begin:%Y-%m-%d}:{end:%Y-%m-%d}"


def parse_meetings_resource_id(resource_id: str) -> tuple[datetime, datetime]:
    """Inverse of :func:`meetings_resource_id` — recover ``[begin, end]`` datetimes.

    ``begin`` opens its day at 00:00:00, ``end`` closes its day at 23:59:59 so the
    recovered window covers both boundary dates whole (matching
    :func:`biennium_window`). Raises ``ValueError`` on a non-meetings id."""
    if not resource_id.startswith(COMMITTEE_MEETINGS_RESOURCE_PREFIX):
        raise ValueError(f"not a committee-meetings resource_id: {resource_id!r}")
    begin_s, end_s = resource_id[len(COMMITTEE_MEETINGS_RESOURCE_PREFIX) :].split(":")
    begin = datetime.combine(date.fromisoformat(begin_s), datetime.min.time())
    end = datetime.combine(date.fromisoformat(end_s), datetime.max.time().replace(microsecond=0))
    return begin, end
