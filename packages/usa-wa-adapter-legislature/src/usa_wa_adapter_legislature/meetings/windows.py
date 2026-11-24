"""Committee-meeting fetch windows + resource-id keying (#39).

The meeting docket (`CommitteeMeetingService.GetCommitteeMeetings`) is fetched per
**date window** so each closed window is a stable cache key fetched once — request
frugality, since WSL is a vital upstream we must not hammer. A biennium maps to its
**filing window** (#597, ``usa_wa_common.legislative_calendar``): the first Monday in
December before it through the day before the next, so December's interim committee days
belong to the upcoming biennium, as in CannObserv/cli. The ``committee-meetings:<begin>:<end>``
resource id keys the raw harvest's cache-or-fetch decision and the archived object. Windows
archived before #597 are calendar years (``2025-01-01:2026-12-31``); they stay in the raw
store under their own ids, and a window's start day still names its biennium on the filing
calendar.

Windows are naive ``datetime``s on the window's Pacific calendar days, because the WSDL
parameter is a bare ``s:dateTime`` and zeep serializes naive values without an offset.
"""

from __future__ import annotations

from datetime import date, datetime, time

from usa_wa_common.legislative_calendar import filing_window

COMMITTEE_MEETINGS_RESOURCE_PREFIX = "committee-meetings:"


def biennium_window(biennium: str) -> tuple[datetime, datetime]:
    """A biennium's ``[begin, end]`` meetings window: its filing window, whole days (#597).

    ``2023-24`` → ``(2022-12-05T00:00:00, 2024-12-01T23:59:59)``.
    """
    first, last = filing_window(biennium)
    return datetime.combine(first, time(0, 0, 0)), datetime.combine(last, time(23, 59, 59))


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
