"""Seat capacity, and the dense `role_periods` dataset that publishes it (#501 D1, D4).

A role is a template, not an occupancy (ONTOLOGY.md § 1), so it has no time axis —
but how many principals it seats does: ``seat:senate:ld-19`` seated five in 1889–90
and one ever since. Capacity therefore lives in its own dataset, one row per period
per role, and the counterpart clip reads the SAME periods, so the clip and the
occupancy gate read one statement of what a seat holds.

**The rule is the occupancy gate's, moved here unchanged** (#360). A Senate
district-year the roster lists with more than one row and **no annotation** seats
that many principals for that biennium; any annotation in the district-year means
one. Of 156 multi-row Senate district-years, 151 carry succession annotations and
5 are bare — all 1889. Ambiguity resolves toward policing, which is the gate's
existing reading. A tenure note printed inside the NAME column (``(On leave of
absence …)``, Kreidler) is an annotation too (accepted limit 7): it is the same
fact, printed one column over, and :func:`strip_tenure_notes` already tells it
apart from name content.

Not keyed on the roster's ``order`` column, which is alphabetical by surname and
would mint seats that never existed (see ``macros/seat_overlap_pairs.sql``'s
history). The House is out of scope: pre-1965 House seats were at-large (#230).

**Dense, across every held role.** Each role's periods tile from the floor of the
biennium holding its earliest published ``valid_from`` — not its earliest key, since
a start can sit before its key biennium (#501 correction 8) — to an open last
period, with no gaps and no overlaps. Party and committee roles get one open
period with no limit, so a consumer's predicate is one rule, "where
``max_holders`` is not null", with no list of kinds to keep in step.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from clearinghouse_domain_legislative.seat_clipping import Capacity, CapacityPeriod
from clearinghouse_domain_legislative.span_kinds import (
    ELECT_KINDS,
    KIND_SENATE,
    SINGLE_HOLDER_KINDS,
)
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_common.legislative_calendar import WA_TERMS
from usa_wa_common.names import strip_tenure_notes

#: The seat kind's own rule: one per Senate seat or House Position, one member-elect per
#: seat-elect role (#600), no limit for a party or committee.
MAX_HOLDERS_KIND = "kind"

#: Counted from the roster's bare multi-row listing (the 1889 Senate).
MAX_HOLDERS_ROSTER = "roster"

#: The closed vocabulary of ``role_periods.max_holders_basis``, in declared order.
MAX_HOLDERS_BASES = (MAX_HOLDERS_KIND, MAX_HOLDERS_ROSTER)

ROLE_PERIOD_SCHEMA = {
    "role_key": "VARCHAR",
    "valid_from": "DATE",
    "valid_to": "DATE",
    "max_holders": "BIGINT",
    "max_holders_basis": "VARCHAR",
}
ROLE_PERIOD_COLUMNS = list(ROLE_PERIOD_SCHEMA)

_ONE_DAY = timedelta(days=1)


def _annotated(row: dict[str, Any]) -> bool:
    """The roster row carries a tenure fact: an annotation, or a note in its name.

    Only non-blank TEXT counts (CR 4): an all-null column can arrive from a frame as
    float NaN, which is truthy, and reading it as an annotation would erase every
    multi-member year."""
    annotation = row.get("annotation")
    if isinstance(annotation, str) and annotation.strip():
        return True
    name = row.get("name")
    return isinstance(name, str) and strip_tenure_notes(name) != name


def bare_multi_member_years(roster: list[dict[str, Any]]) -> dict[int, dict[int, int]]:
    """Senate ``district → {year: rows}`` for every bare multi-row district-year."""
    rows: dict[tuple[int, int], int] = defaultdict(int)
    annotated: set[tuple[int, int]] = set()
    for row in roster:
        if row.get("chamber") != "senate":
            continue
        key = (int(row["district"]), int(row["year"]))
        rows[key] += 1
        if _annotated(row):
            annotated.add(key)
    out: dict[int, dict[int, int]] = defaultdict(dict)
    for (district, year), count in sorted(rows.items()):
        if count > 1 and (district, year) not in annotated:
            out[district][year] = count
    return dict(out)


def _periods(roster: list[dict[str, Any]]) -> dict[tuple[str, str], list[CapacityPeriod]]:
    """Every multi-holder period, by seat, oldest first."""
    out: dict[tuple[str, str], list[CapacityPeriod]] = {}
    for district, years in bare_multi_member_years(roster).items():
        periods = []
        for year, count in sorted(years.items()):
            biennium = biennium_for_date(date(year, 1, 1))
            periods.append(CapacityPeriod(WA_TERMS.start(biennium), WA_TERMS.end(biennium), count))
        out[(KIND_SENATE, str(district))] = periods
    return out


def seat_capacity(roster: list[dict[str, Any]]) -> Capacity:
    """The clip's input (:func:`~clearinghouse_domain_legislative.seat_clipping.
    clip_seat_counterparts`): the seats that hold more than one principal, and when.
    A seat it omits holds one."""
    return {seat: tuple(periods) for seat, periods in _periods(roster).items()}


def _row(role_key: str, frm: date, to: date | None, holders: int | None, basis: str) -> dict:
    return {
        "role_key": role_key,
        "valid_from": frm,
        "valid_to": to,
        "max_holders": holders,
        "max_holders_basis": basis,
    }


def role_period_rows(
    assignments: list[dict[str, Any]], roster: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The published ``role_periods`` rows: dense, tiled, ordered by role then start."""
    first: dict[str, date] = {}
    seat: dict[str, tuple[str, str]] = {}
    for row in assignments:
        role_key = str(row["role_key"])
        valid_from = row["valid_from"]
        if role_key not in first or valid_from < first[role_key]:
            first[role_key] = valid_from
        seat[role_key] = (str(row["span_kind"]), str(row["span_discriminator"]))
    multi = _periods(roster)
    out: list[dict[str, Any]] = []
    for role_key in sorted(first):
        # The term holding the first tenure's start (#598): a pre-term January seating is
        # still inside a period, so `role_periods_tiled` never sees it uncovered.
        floor = WA_TERMS.start(WA_TERMS.biennium_on(first[role_key]))
        kind, discriminator = seat[role_key]
        if kind in ELECT_KINDS:
            # #600: from the first election day, which lies before the term it is won for. A
            # dbt frame hands a Timestamp; the column is DATE.
            day = first[role_key]
            day = day.date() if isinstance(day, datetime) else day
            out.append(_row(role_key, day, None, 1, MAX_HOLDERS_KIND))
            continue
        if kind not in SINGLE_HOLDER_KINDS:
            out.append(_row(role_key, floor, None, None, MAX_HOLDERS_KIND))
            continue
        cursor = floor
        for period in multi.get((kind, discriminator), []):
            if period.valid_to is not None and period.valid_to < cursor:
                continue  # before the seat's first holder: nothing asserted (limit 4)
            if period.valid_from > cursor:
                out.append(
                    _row(role_key, cursor, period.valid_from - _ONE_DAY, 1, MAX_HOLDERS_KIND)
                )
            start = max(period.valid_from, cursor)
            out.append(
                _row(role_key, start, period.valid_to, period.max_holders, MAX_HOLDERS_ROSTER)
            )
            if period.valid_to is None:
                break
            cursor = period.valid_to + _ONE_DAY
        else:
            out.append(_row(role_key, cursor, None, 1, MAX_HOLDERS_KIND))
    return out
