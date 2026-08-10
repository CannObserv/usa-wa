"""C1a committee lifecycle-window derivation (usa-wa#124).

The **objective** half of the committee lineage/lifecycle model: from each committee
WSL ``Id``'s roster-presence across the archived bienniums, derive its operational
window as PM ``founded`` / ``dissolved`` entity events.

Per-``Id``, no lineage grouping:

- ``founded`` = the start year of the first biennium the committee appears in — but
  **floor-gated**: omitted when the committee is present in the *earliest archived*
  biennium, because it may predate the archive and its true founding is unknown (the
  1999-00 floor case). A committee absent from the floor biennium but present later
  genuinely began then (we archived the floor and it was not there).
- ``dissolved`` = the end year of the last observed biennium — but **only for a
  non-current committee**. A committee present in the current biennium is the live
  head and carries no ``dissolved``.

The biennium boundary is the documented approximation for an otherwise-unpublished
date (mirrors :func:`~usa_wa_adapter_legislature.synthesis.biennium_start_date`, #46).

Pure derivation (:func:`derive_committee_windows`) + a thin archive collector
(:func:`collect_committee_presence`); the event producer (C3) turns each
:class:`CommitteeWindow` into observations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from clearinghouse_domain_legislative.terms import parse_biennium


@dataclass(frozen=True)
class CommitteeWindow:
    """One committee ``Id``'s derived operational window.

    ``founded_year``/``dissolved_year`` are ``None`` when not assertable (floor-gated
    founding, or a still-current committee's open end).
    """

    source_id: str
    is_current: bool
    founded_year: int | None
    dissolved_year: int | None


def _start_year(biennium: str) -> int:
    return parse_biennium(biennium)[0]


def build_founded_floors(links: Iterable[Any]) -> dict[str, int]:
    """``{source_id: earliest rename year}`` — the back-stamp founded correction (#128).

    From the C2 succession links, the year a committee first came into existence under its
    own ``Id``: the **successor** (``linked``) of a ``succeeded_by`` re-key, or the
    **child** (``subject``) of a ``split_from``. ``merged_with`` is excluded — the survivor
    pre-existed the merge. A committee with multiple incoming renames takes the earliest.
    WSL back-stamps a re-keyed ``Id`` onto the prior biennium, so its roster-first year is
    one biennium early; the rename link carries the true year (attested during #124)."""
    floors: dict[str, int] = {}

    def _bump(source_id: str, year: int) -> None:
        cur = floors.get(source_id)
        floors[source_id] = year if cur is None else min(cur, year)

    for link in links:
        year = link.effective_year
        if year is None:
            continue
        if link.slug == "succeeded_by":
            _bump(link.linked_source_id, year)
        elif link.slug == "split_from":
            _bump(link.subject_source_id, year)
    return floors


def derive_committee_windows(
    presence_by_id: Mapping[str, Iterable[str]],
    *,
    current_biennium: str,
    archived_bienniums: Sequence[str],
    founded_floors: Mapping[str, int] | None = None,
) -> dict[str, CommitteeWindow]:
    """Derive each committee ``Id``'s :class:`CommitteeWindow` from its roster presence.

    ``presence_by_id`` maps a committee ``Id`` to the bienniums it appears in.
    ``archived_bienniums`` is the archive's domain (any order); its earliest member is
    the founding floor. An ``Id`` with no presence is skipped.

    ``founded_floors`` (#128, from :func:`build_founded_floors`) corrects a back-stamped
    re-key: a committee's ``founded`` is bumped **forward** to its attested rename year
    when that is later than its roster-first biennium (``max`` — never earlier than roster
    evidence, so a normally-keyed committee is unchanged). The floor-gate still wins (a
    floor-present committee stays ``None``)."""
    floors = founded_floors or {}
    floor_start = min((_start_year(b) for b in archived_bienniums), default=None)
    windows: dict[str, CommitteeWindow] = {}
    for source_id, raw_bienniums in presence_by_id.items():
        bienniums = sorted(set(raw_bienniums), key=_start_year)
        if not bienniums:
            continue
        first, last = bienniums[0], bienniums[-1]
        is_current = current_biennium in bienniums
        # Founded only when the committee is absent from the earliest archived
        # biennium (so it genuinely began after the floor); else unknowable.
        founded_year = (
            _start_year(first)
            if floor_start is not None and _start_year(first) > floor_start
            else None
        )
        # Back-stamp correction: bump founded forward to the attested rename year (#128).
        if founded_year is not None:
            override = floors.get(source_id)
            if override is not None and override > founded_year:
                founded_year = override
        dissolved_year = None if is_current else parse_biennium(last)[1]
        windows[source_id] = CommitteeWindow(
            source_id=source_id,
            is_current=is_current,
            founded_year=founded_year,
            dissolved_year=dissolved_year,
        )
    return windows


class _RosterProvider(Protocol):
    async def archived_bienniums(self) -> list[str]: ...

    async def roster_records(self, biennium: str) -> list[dict[str, Any]]: ...


async def collect_committee_presence(provider: _RosterProvider) -> dict[str, set[str]]:
    """``{source_id: {biennium, …}}`` — which bienniums each committee ``Id`` appears in.

    Reads the archive-first :class:`CommitteeRosterCohortProvider` surface: every
    archived biennium's committee records, keyed by the stable WSL ``Id``.
    """
    presence: dict[str, set[str]] = {}
    for biennium in await provider.archived_bienniums():
        for rec in await provider.roster_records(biennium):
            source_id = rec.get("Id")
            if source_id is None:
                continue
            presence.setdefault(str(source_id), set()).add(biennium)
    return presence
