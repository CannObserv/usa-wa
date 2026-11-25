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
begins, whatever the filing window says: from 1981, the second Monday in January (#598),
and calendar years before the floor (#604).

Regular and Special Sessions are children *within* a filing window, with dates from statute
(RCW 44.04.010) and each session's agreed calendar. They are not modelled here (ONTOLOGY §4).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from clearinghouse_domain_legislative.terms import parse_biennium

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


#: The first biennium whose terms start on RCW 44.04.021's date (1981 c 288 s 68). Earlier
#: terms keep calendar-year edges until #604 reads the 1891 and pre-1981 session laws
#: (owner decision Q1, 2026-11-24).
STATUTORY_TERM_FLOOR = 1981


def _second_monday_in_january(year: int) -> date:
    january_first = date(year, 1, 1)
    return january_first + timedelta(days=(7 - january_first.weekday()) % 7 + 7)


def term_start(label: str) -> date:
    """The day ``label``'s legislators' regular terms commence (#598).

    RCW 44.04.021: "the second Monday in January following the date of election", from the
    statute's 1981 floor (:data:`STATUTORY_TERM_FLOOR`); Jan 1 of the biennium's first year
    before it. 2025-26 → 2025-01-13; 2027-28 → 2027-01-11."""
    start, _ = parse_biennium(label)
    if start < STATUTORY_TERM_FLOOR:
        return date(start, 1, 1)
    return _second_monday_in_january(start)


def term_end(label: str) -> date:
    """The last day of ``label``'s terms: the day before the next biennium's terms commence.
    A legislator serves until the successor's term begins, so 2025-26 ends 2027-01-10, and
    1979-80 (the floor's seam) ends 1981-01-11."""
    start, _ = parse_biennium(label)
    return term_start(_label(start + 2)) - timedelta(days=1)


def term_biennium_on(day: date) -> str:
    """The biennium whose legislators are serving on ``day``: the clock spans, memberships
    and committee ``active`` follow (#597, #598).

    It flips on the second Monday in January of the odd year (calendar years before the 1981
    floor). Between the filing flip (first Monday in December) and the term flip, it lags
    :func:`filing_biennium_on` by one biennium. A ``datetime`` (a pandas ``Timestamp`` from a
    dbt model included) is read as its day."""
    if isinstance(day, datetime):
        day = day.date()
    odd = day.year if day.year % 2 else day.year - 1
    label = _label(odd)
    return label if day >= term_start(label) else _label(odd - 2)


def harvest_biennium(today: date | None = None) -> str:
    """The biennium a raw harvest targets: ``USA_WA_BIENNIUM`` when pinned, else the filing
    biennium of ``today`` (a Pacific day), or of the clock when ``today`` is ``None`` (#597).

    One rule for the WSL, PDC and SOS harvests, so they cannot disagree about which biennium
    is current on the night the filing window turns."""
    pinned = os.environ.get("USA_WA_BIENNIUM")
    if pinned:
        return pinned
    return filing_biennium_on(today) if today else filing_biennium(datetime.now(UTC))


@dataclass(frozen=True)
class WaTerms:
    """WA's :class:`~clearinghouse_domain_legislative.terms.TermCalendar` (#598): the
    statute's term from 1981 and calendar years before it. The span engine takes it as a
    parameter, which keeps the engine jurisdiction-agnostic and the statute in WA's own
    vocabulary."""

    def start(self, label: str) -> date:
        """:func:`term_start`."""
        return term_start(label)

    def end(self, label: str) -> date:
        """:func:`term_end`."""
        return term_end(label)

    def biennium_on(self, day: date) -> str:
        """:func:`term_biennium_on`."""
        return term_biennium_on(day)


#: The term calendar every WA span is built on.
WA_TERMS = WaTerms()
