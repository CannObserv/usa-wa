"""The biennium term calendar — pure label arithmetic over ``YYYY-YY`` biennia (#189).

A **biennium** is the two-year term a biennial legislature keys its sessions, rosters and
tenures on; this module is its label arithmetic, so the span engine next door can do term
algebra without importing a jurisdiction adapter.

It lived in ``usa_wa_adapter_legislature.synthesis`` — the WSL *anchor synthesizer* — purely
because that is where the first caller needed it, which is how the WSL adapter became a
de-facto shared kernel (AR-14): ``usa-wa-adapter-pdc``, ``usa-wa-adapter-sos`` and
``usa-wa-sync-powermap`` all reached into a SOAP adapter for date arithmetic. Nothing here
touches a wire, a session, or WSL.

**Label convention.** ``YYYY-YY`` — the odd start year in full, the even end year's last two
digits (``2025-26``). Terms begin on odd years, so consecutive biennia are exactly two years
apart and labels sort chronologically as plain strings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol

_BIENNIUM_RE = re.compile(r"^(\d{4})-(\d{2})$")


def parse_biennium(biennium: str) -> tuple[int, int]:
    """Parse a ``YYYY-YY`` biennium label into ``(start_year, end_year)``.

    ``2025-26`` → ``(2025, 2026)``. The end year is reconstructed from the
    start year's century, supporting decade rollovers (``2029-30``).
    """
    match = _BIENNIUM_RE.match(biennium)
    if match is None:
        raise ValueError(f"invalid biennium label: {biennium!r} (expected YYYY-YY)")
    start = int(match.group(1))
    end_suffix = int(match.group(2))
    century = (start // 100) * 100
    end = century + end_suffix
    if end < start:
        end += 100
    return start, end


def biennium_for_date(today: date) -> str:
    """Compute the biennium label (``YYYY-YY``) covering ``today``.

    Bienniums begin on odd years (2025-26, 2027-28, …). On an even year we
    roll back to the prior odd year.
    """
    start = today.year if today.year % 2 == 1 else today.year - 1
    end_suffix = (start + 1) % 100
    return f"{start}-{end_suffix:02d}"


def biennium_start_year(label: str) -> int:
    """Parse the odd start year from a ``YYYY-YY`` biennium label."""
    return int(label.split("-", 1)[0])


def biennium_start_date(label: str) -> date:
    """The date a biennium begins — Jan 1 of its odd start year.

    WSL exposes no explicit committee name-change date; this biennium-start boundary
    is the documented approximation used to window a detected rename (#46).
    """
    return date(biennium_start_year(label), 1, 1)


def previous_biennium(label: str) -> str:
    """The biennium two years before ``label`` (the rename diff's "before" side, #46)."""
    start = biennium_start_year(label) - 2
    return f"{start}-{(start + 1) % 100:02d}"


def next_biennium(label: str) -> str:
    """The biennium two years after ``label``: the one WSL publishes ahead of a rollover
    (#599 — 2027-28 answered on 2026-11-24, while 2025-26 was current)."""
    start = biennium_start_year(label) + 2
    return f"{start}-{(start + 1) % 100:02d}"


def bienniums_in_range(from_biennium: str, to_biennium: str) -> list[str]:
    """Inclusive list of biennium labels from ``from_biennium`` to ``to_biennium``.

    Bienniums start on odd years; the range walks by 2. ``from`` must not be after
    ``to``."""
    start = biennium_start_year(from_biennium)
    end = biennium_start_year(to_biennium)
    if start > end:
        raise ValueError(f"from-biennium {from_biennium!r} is after to-biennium {to_biennium!r}")
    return [f"{y}-{(y + 1) % 100:02d}" for y in range(start, end + 1, 2)]


class TermCalendar(Protocol):
    """When a jurisdiction's legislative terms start and end (#598).

    The span engine is jurisdiction-agnostic, so the calendar is a parameter: an observed
    tenure edge is a term's first or last day, and a dated event belongs to the term that
    holds it. ``end(label)`` is the term's LAST day, inclusive: the day before the next
    term's ``start``."""

    def start(self, label: str) -> date:
        """The first day of ``label``'s terms."""
        ...

    def end(self, label: str) -> date:
        """The last day of ``label``'s terms."""
        ...

    def biennium_on(self, day: date) -> str:
        """The biennium whose terms hold ``day``."""
        ...


@dataclass(frozen=True)
class CalendarYearTerms:
    """Terms on calendar years: Jan 1 of the odd year to Dec 31 of the even one. This is the
    engine's default, and was every jurisdiction's edge before #598 gave WA its statute."""

    def start(self, label: str) -> date:
        """Jan 1 of the biennium's first year."""
        return date(parse_biennium(label)[0], 1, 1)

    def end(self, label: str) -> date:
        """Dec 31 of the biennium's second year."""
        return date(parse_biennium(label)[1], 12, 31)

    def biennium_on(self, day: date) -> str:
        """The calendar biennium holding ``day``."""
        return biennium_for_date(day)


#: The default term calendar: calendar years.
CALENDAR_YEAR = CalendarYearTerms()
