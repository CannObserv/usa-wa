"""The WA general-election calendar — which cycles decide a biennium's membership (#189).

Pure biennium↔election-year arithmetic. Nothing here is about a *publisher* of election
data; it is about how Washington elects and seats its legislature, which is why the last of
these functions already documented itself as "a property of the layer rather than a
per-source patch" while sitting inside `usa_wa_adapter_pdc.adapter` — the SODA client
module. Four `usa-wa-adapter-sos` modules imported the PDC adapter to reach it.

WA holds a general election **every** November, not only in even years:

- the **even** ``start - 1`` cycle seats the chamber for the biennium starting ``start``;
- the **odd** ``start`` November fills mid-biennium vacancies by special (#121 — Nov 2025
  seated Hunt in the LD5 Senate plus four House appointees);
- November of ``start + 1`` is deliberately excluded — it seats the *next* biennium. Once it
  has been held, :func:`lookahead_election_year` names it, so its winners are captured before
  the Jan 1 rollover makes it the next biennium's own (#135).
"""

from __future__ import annotations

from datetime import date, timedelta

from clearinghouse_domain_legislative.terms import parse_biennium


def election_year_for_biennium(biennium: str) -> int:
    """The general-election year that seated a biennium's House — its odd start year
    minus one (WA House is entirely up every even November). ``2025-26`` → ``2024``."""
    start_year, _ = parse_biennium(biennium)
    return start_year - 1


def seating_biennium_for_election_year(election_year: int) -> str:
    """The biennium a general election's winners sit in. An **even** year seats the biennium
    starting the following odd year (``2012`` → ``"2013-14"``); an **odd** year is a
    mid-biennium special seating the biennium *starting that year* (``2025`` → ``"2025-26"``,
    #121 — Nov 2025 seated Hunt/Krishnadasan/Zahn). Because of the odd branch this is no longer
    the strict inverse of :func:`election_year_for_biennium` (which stays even/seating-only).
    Used by the #79 backfill to era-match each cohort to the roster it seated, fixing the #75
    current-snapshot limitation."""
    start = election_year + 1 if election_year % 2 == 0 else election_year
    return f"{start}-{(start + 1) % 100:02d}"


def election_years_for_biennium(biennium: str) -> list[int]:
    """Every general-election year a biennium's membership can be decided by (#106).

    WA holds a general election **every** November, not only in even years: the even ``start-1``
    cycle seats the chamber, and the odd ``start`` November fills mid-biennium vacancies by
    special (Nov 2025 seated Hunt in the LD5 Senate and four House appointees). November of
    ``start+1`` is deliberately excluded — it seats the *next* biennium.

    The seating year leads, so a consumer archiving in list order writes the even cohort first.
    This is the shared era helper both odd-year sweeps derive from (the SOS results
    harvest/refresh, #106; the PDC refresh/discover, #121), so "every general election year" is
    a property of the layer rather than a per-source patch."""
    start_year, _ = parse_biennium(biennium)
    return [start_year - 1, start_year]


def senate_election_years_for_biennium(biennium: str) -> tuple[int, int, int]:
    """The general-election years whose winners sit in a biennium's Senate (#75/#121).

    WA Senate is staggered 4-year terms — only ~half the chamber is up each even November —
    so identifying *all* sitting senators requires the union of the two most-recent even
    years: ``start-1`` (seats up that cycle) and ``start-3`` (seats still mid-term). The odd
    ``start`` November additionally fills mid-biennium vacancies by special (#121 — Nov 2025
    seated Hunt LD5 / Krishnadasan LD26), so its cohort completes the sitting union; it is the
    only cohort that can *change* during the biennium (older odd specials are already archived
    and the builder reads every archived cohort). For ``2025-26``: ``(2024, 2022, 2025)``."""
    start_year, _ = parse_biennium(biennium)
    return (start_year - 1, start_year - 3, start_year)


def general_election_day(election_year: int) -> date:
    """A year's WA general-election day: the first Tuesday after the first Monday of November.
    ``2026`` → ``2026-11-03``; ``2022`` → ``2022-11-08`` (Nov 1 fell on a Tuesday)."""
    first_monday = date(election_year, 11, 1)
    while first_monday.weekday() != 0:
        first_monday += timedelta(days=1)
    return first_monday + timedelta(days=1)


def lookahead_election_year(biennium: str, *, today: date) -> int | None:
    """The NEXT biennium's seating election (``start + 1``) once it has been held, else ``None``.

    The early-capture year (#135). :func:`election_years_for_biennium` excludes November of
    ``start + 1`` because it seats the next biennium — so a date-current harvest would not fetch
    its winners (PDC, early Dec) or certified results (SOS, early Dec) until Jan 1. Harvesting it
    as well from the day **after** election day closes that gap. Not election day itself: the
    nightly runs 08:00 UTC, midnight in WA, before any returns exist.

    Raw capture only. Every consumer era-matches a cohort to the biennium it seats, which has no
    roster until the rollover, so nothing is derived from it early. From Jan 1 the year leads the
    next biennium's own decisive set, so the hand-off needs no switch."""
    start_year, _ = parse_biennium(biennium)
    year = start_year + 1
    return year if today > general_election_day(year) else None
