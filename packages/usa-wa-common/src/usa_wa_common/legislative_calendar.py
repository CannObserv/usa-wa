"""The two WA legislative clocks (#597): the **filing window** and the **term**.

"Which biennium is it?" has two answers in December of an even year, and the pipeline needs
both.

**The filing window** is the cohort's rule, the one ``CannObserv/cli`` uses
(``co_cli/ext/wa_leg/calendar.py``, cli#1268, operator rule 2026-11-10). Biennium
``YYYY-YY`` opens at 00:00 Pacific on the **first Monday in December** of ``YYYY - 1``, when
bill pre-filing for its first session opens, and runs through the day before the next
biennium's first Monday in December. So 2025-26 is 2024-12-02 → 2026-12-06 and 2027-28 is
2026-12-07 → 2028-12-03, with no gap and no overlap. It is a filing convenience: December's
pre-filed bills and December's committee meeting days belong to the *upcoming* biennium.
It decides what the harvests target and which biennium a meeting window belongs to. WSL
agrees with it in practice: it published 2027-28 on 2026-11-24.

**The term** is when legislators serve. RCW 44.04.021 starts every regular term on the second
Monday in January after the election. That is the clock seats, memberships and committee
``active`` follow (the owner, 2026-11-24): a legislator serves until the successor's term
begins, whatever the filing window says. Until #598 makes it statutory it is still the
calendar year (``biennium_for_date``), which is what every published span edge rests on.

Regular and Special Sessions are children *within* a filing window, with dates from statute
(RCW 44.04.010) and each session's agreed calendar. They are not modelled here (ONTOLOGY §4).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from clearinghouse_domain_legislative.terms import biennium_for_date, parse_biennium

#: The Legislature's wall clock: every filing-window edge is a Pacific calendar day.
SITE_TZ = ZoneInfo("America/Los_Angeles")


def _first_monday_in_december(year: int) -> date:
    """The day WA bill pre-filing opens for the session of ``year + 1``."""
    december_first = date(year, 12, 1)
    return december_first + timedelta(days=(7 - december_first.weekday()) % 7)


def _label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def filing_biennium_on(day: date) -> str:
    """The biennium whose filing window holds the Pacific calendar day ``day``.

    The calendar year's odd biennium, rolled forward from the first Monday in December:
    2026-12-06 is 2025-26 and 2026-12-07 is 2027-28."""
    year = day.year + 1 if day >= _first_monday_in_december(day.year) else day.year
    return _label(year if year % 2 else year - 1)


def filing_biennium(moment: datetime) -> str:
    """The filing biennium of an aware ``moment``, judged on the Pacific calendar.

    The nightly fires at 08:00 UTC, which on 2026-12-07 is exactly 00:00 PST: that run is the
    first of 2027-28. A naive datetime is refused (all UTC, AGENTS.md): it names no calendar."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"filing_biennium needs an aware datetime, got naive {moment!r}")
    return filing_biennium_on(moment.astimezone(SITE_TZ).date())


def filing_window(label: str) -> tuple[date, date]:
    """``label``'s filing window as ``(first day, last day)``, both inclusive Pacific days.

    Refuses a label that is not a biennium: an even first year (``2026-27``) or a second year
    that does not follow the first would overlap two real ones."""
    start, end = parse_biennium(label)
    if start % 2 == 0 or end != start + 1:
        raise ValueError(f"{label!r} is not a WA biennium (odd first year, then the next)")
    first = _first_monday_in_december(start - 1)
    last = _first_monday_in_december(start + 1) - timedelta(days=1)
    return first, last


def term_biennium_on(day: date) -> str:
    """The biennium whose legislators are serving on ``day``: the clock spans, memberships
    and committee ``active`` follow.

    Still the calendar year (Jan 1) until #598 moves it to the statutory term start, the
    second Monday in January (RCW 44.04.021). Between the filing flip and the term flip,
    this lags :func:`filing_biennium_on` by one biennium."""
    return biennium_for_date(day)
