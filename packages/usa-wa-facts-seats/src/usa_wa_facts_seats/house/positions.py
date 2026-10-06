"""A biennium's House position map: even seating candidacies plus odd-special winners.

Pure. Read by the #302 pipeline's conformed House model. It left
``usa_wa_facts_seats.house.build``, the Postgres write path deleted in #412 PR F, so the
pipeline could compose the map without importing it.
"""

from __future__ import annotations

from usa_wa_common.ballot import HousePositionsByLd
from usa_wa_common.elections import election_years_for_biennium


def biennium_election_years(biennium: str) -> tuple[int, int]:
    """``(even_seating_year, odd_special_year)`` for a biennium (#123).

    Public since #309: the conformed tier composes the same map, and a second
    implementation of the even/odd split is a divergence waiting to happen. ``election_years_for_
    biennium`` returns ``[start-1, start]`` — the even November that seats the chamber and the odd
    November that fills mid-biennium vacancies by special. ``even == odd`` never happens (start-1 is
    always even), so the two are distinct sources to merge."""
    years = election_years_for_biennium(biennium)
    return years[0], years[-1]


def merge_positions(
    biennium: str,
    positions: dict[int, HousePositionsByLd],
    house_winners: dict[int, HousePositionsByLd],
) -> HousePositionsByLd:
    """The biennium's House position map = even seating candidacies ∪ odd-special **winners**
    (#123 §1). ``position_for`` is name-keyed, so appended entries only *add* resolution power —
    nothing existing is retracted. The even seating cohort keeps its full candidacy set (the #103
    elimination depends on the losers); only the odd side is winner-filtered (hazard b — a losing
    special candidacy must not false-match a member). An absent/empty odd cohort (no special that
    biennium, or the odd November not yet held) leaves the even map unchanged — backward
    compatible with the pre-#123 single-year lookup."""
    even_year, odd_year = biennium_election_years(biennium)
    merged: HousePositionsByLd = {
        ld: list(entries) for ld, entries in positions.get(even_year, {}).items()
    }
    for ld, entries in house_winners.get(odd_year, {}).items():
        merged.setdefault(ld, []).extend(entries)
    return merged
