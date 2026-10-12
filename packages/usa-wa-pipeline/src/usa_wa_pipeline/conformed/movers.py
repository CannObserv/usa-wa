"""A House→Senate move inside one biennium, dated from the Senate seating (usa-wa#490) — pure.

WSL lists a mid-biennium mover in **both** chambers for the biennium they switch in:
a House row and a named Senate row under one stable ``Id``, and committee rows in
both chambers for the whole biennium. The #105 (a) mover exclusion drops the House
row so the #103 elimination can seat the appointed successor — and that was the
whole of it. Nothing carried the mover's own House tenure into the biennium, so it
ended at the previous biennium's ceiling: June Robinson held LD-38 Pos. 1 until her
Senate appointment on 2020-05-13, and her span ended 2018-12-31. Eleven more members
measured the same way (McDermott 2007 to Alvarado 2025), every one confirmed by the
roster PDF's "Resigned; Appointed to the Senate". Her House committee spans, for
their part, ran on to 2020-12-31.

The date is already curated: every such move carries an operator-recorded
``seated chamber-senate`` event (the #145 tranches and the #226 roster backfill,
each with its evidence URL). A mover with an explicit ``vacated chamber-house`` (the
#145 recipe) is dated by it too. A mover with neither is logged and left alone,
because the biennium floor is not when anybody moved.

Two consumers:

- the House family carries the tenure (:func:`carry_mover_house_tenures`) — as an
  **observation**, after the elimination has run, so it merges into the tenure it
  continues under that tenure's own key and the successor's seating is untouched;
- the committee family re-dates the mover's committees (:func:`date_mover_committees`):
  House committees end with the House seat, Senate committees begin with the Senate
  seat. Keys never move; only boundaries do.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.operator_events import KIND_SEATED, KIND_VACATED
from clearinghouse_domain_legislative.operator_overlay import SuccessionEvent
from clearinghouse_domain_legislative.span_kinds import KIND_COMMITTEE, KIND_HOUSE, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import Observation, TenureSpan
from clearinghouse_domain_legislative.terms import (
    biennium_for_date,
    biennium_start_date,
    previous_biennium,
)
from usa_wa_common.ballot import HousePositionsByLd, position_for
from usa_wa_common.seats import house_span_discriminator, parse_house_span_discriminator
from usa_wa_facts_seats.house.backchain import REDISTRICTING_ERA_START_BIENNIA
from usa_wa_facts_seats.house.roster import (
    HouseRosterEntry,
    build_house_roster,
    house_mover_ids,
)

logger = get_logger(__name__)

#: ``committee_agency`` values, as staging carries the WSL ``Agency`` field. A Joint
#: committee belongs to neither chamber, so a move dates none of its seats.
AGENCY_HOUSE = "House"
AGENCY_SENATE = "Senate"


@dataclass(frozen=True)
class ChamberMove:
    """One member's House→Senate move inside ``biennium``.

    ``moved_on`` is the Senate seating, else the earliest explicit House vacate:
    when the Senate tenure began. The House tenure ended then or earlier — a
    successor's seating can prove the vacancy came first.

    ``ld`` is the district of the mover's House row that biennium; ``ballot_seat``
    the House discriminator the biennium's own ballot positions them in, if any."""

    member_id: str
    biennium: str
    moved_on: date
    ld: int | None = None
    ballot_seat: str | None = None


def chamber_moves(
    rows_by_biennium: dict[str, list[dict[str, Any]]],
    events: Iterable[SuccessionEvent],
    *,
    positions_by_biennium: dict[str, HousePositionsByLd] | None = None,
) -> list[ChamberMove]:
    """Every dated move: a #105 mover (:func:`house_mover_ids`, the signal the House
    roster excludes on) with a ``seated chamber-senate`` or ``vacated chamber-house``
    event inside that biennium, after its first day.

    ``positions_by_biennium`` (the House family's merged #123 maps) positions the
    mover from their own House row — the lookup the elimination would have made had
    the exclusion not removed them from its roster."""
    positions_by_biennium = positions_by_biennium or {}
    seatings: dict[tuple[str, str], date] = {}
    vacates: dict[tuple[str, str], date] = {}
    for event in events:
        if _is_senate_seating(event):
            target = seatings
        elif _is_house_vacate(event):
            target = vacates
        else:
            continue
        key = (event.member_id, biennium_for_date(event.effective_date))
        if event.effective_date > biennium_start_date(key[1]):
            target[key] = min(target.get(key, event.effective_date), event.effective_date)
    moves: list[ChamberMove] = []
    for biennium, rows in sorted(rows_by_biennium.items()):
        movers = house_mover_ids(rows)
        # keep_ids: the movers' own rows, which the exclusion would otherwise drop.
        roster = build_house_roster(rows, keep_ids=movers)
        positions = positions_by_biennium.get(biennium, {})
        for member_id in sorted(movers):
            key = (member_id, biennium)
            moved_on = seatings.get(key) or vacates.get(key)
            if moved_on is None:
                logger.info(
                    "chamber_move_undated", extra={"member_id": member_id, "biennium": biennium}
                )
                continue
            ld, ballot_seat = _mover_seat(roster, positions, member_id)
            moves.append(
                ChamberMove(
                    member_id=member_id,
                    biennium=biennium,
                    moved_on=moved_on,
                    ld=ld,
                    ballot_seat=ballot_seat,
                )
            )
    return moves


def carry_mover_house_tenures(
    observations: list[Observation],
    moves: Iterable[ChamberMove],
    events: Iterable[SuccessionEvent],
) -> tuple[list[Observation], list[SuccessionEvent]]:
    """``observations`` plus each mover's House seat carried into the move biennium,
    and the ``vacated`` events that close the carried tenures.

    The seat is the move biennium's own ballot Position (``ballot_seat``) when it
    names one — evidence, where the previous biennium's seat is an assumption. That
    assumption is made only within one district map and one LD: the back-chain's
    own stop (#118/#140), since a new map keeps the LD number but not the district.
    Otherwise nothing is carried (``chamber_move_house_seat_unknown``); a mover
    appointed into the House in the move biennium (O'Ban) has neither, and the
    #145 ``vacated`` synthesis is theirs. An explicit ``vacated chamber-house`` in
    the move biennium already dates the end, so none is implied; one naming a
    different seat is left to that synthesis.

    The implied vacate is the Senate seating, or a successor's earlier dated
    seating on the same seat. A seat holds one person at a time, so the successor's
    appointment proves the vacancy (Liias: House successor appointed 2014-01-21,
    Senate seating 2014-01-22); ending at the later date would publish a one-day
    overlap with both edges dated, which the #360 clip declines and the occupancy
    gate counts.
    """
    events = list(events)
    held = {(o.member_id, o.biennium): o for o in observations if o.kind == KIND_HOUSE}
    carried = list(observations)
    implied: list[SuccessionEvent] = []
    for move in moves:
        prior = held.get((move.member_id, previous_biennium(move.biennium)))
        seat = move.ballot_seat or _continued_seat(move, prior)
        if seat is None:
            if prior is not None:
                logger.info(
                    "chamber_move_house_seat_unknown",
                    extra={"member_id": move.member_id, "biennium": move.biennium},
                )
            continue
        explicit = {
            e.seat_discriminator
            for e in events
            if _is_house_vacate(e)
            and e.member_id == move.member_id
            and biennium_for_date(e.effective_date) == move.biennium
        }
        if explicit - {seat}:
            continue
        carried.append(Observation(move.member_id, KIND_HOUSE, seat, move.biennium))
        logger.info(
            "chamber_move_house_tenure_carried",
            extra={"member_id": move.member_id, "seat": seat, "biennium": move.biennium},
        )
        if explicit:
            continue
        implied.append(
            SuccessionEvent(
                member_id=move.member_id,
                kind=KIND_VACATED,
                effective_date=_house_end(move, seat, events),
                seat_kind=KIND_HOUSE,
                seat_discriminator=seat,
            )
        )
    return carried, implied


def date_mover_committees(
    spans: list[TenureSpan],
    moves: Iterable[ChamberMove],
    agencies: dict[tuple[str, str], str],
    *,
    house_spans: Iterable[TenureSpan],
) -> list[TenureSpan]:
    """``spans`` with each mover's committee seats dated at the move (order kept).

    A House committee running through the move ends with the House seat — the
    member's ``chamber-house`` span ending inside the move biennium, else the move
    itself. A Senate committee the member's wire opens on the move biennium's floor
    begins at the move. A Joint committee is neither chamber's, and nothing outside
    the move biennium is touched."""
    house_end = _house_ends(house_spans)
    by_member: dict[str, list[ChamberMove]] = {}
    for move in moves:
        by_member.setdefault(move.member_id, []).append(move)
    out: list[TenureSpan] = []
    for span in spans:
        for move in by_member.get(span.member_id, []):
            if span.kind == KIND_COMMITTEE:
                agency = agencies.get((move.biennium, span.discriminator))
                end = house_end.get((move.member_id, move.biennium), move.moved_on)
                span = _date_committee(span, move, agency, end)
        out.append(span)
    return out


def committee_agencies(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """Staging committee-member rows → ``{(biennium, committee_id): committee_agency}``."""
    out: dict[tuple[str, str], str] = {}
    for row in rows:
        biennium, committee_id = row.get("biennium"), row.get("committee_id")
        agency = row.get("committee_agency")
        if biennium and committee_id and agency:
            out[(str(biennium), str(committee_id))] = str(agency)
    return out


def _date_committee(
    span: TenureSpan, move: ChamberMove, agency: str | None, house_end: date
) -> TenureSpan:
    """One committee span re-dated by one move, or unchanged."""
    if (
        agency == AGENCY_HOUSE
        and span.end_biennium == move.biennium
        and span.valid_from <= house_end
        and (span.valid_to is None or house_end < span.valid_to)
    ):
        return replace(span, valid_to=house_end, is_active=False)
    if (
        agency == AGENCY_SENATE
        and span.start_biennium == move.biennium
        and span.valid_from == biennium_start_date(move.biennium)
        and (span.valid_to is None or move.moved_on <= span.valid_to)
    ):
        return replace(span, valid_from=move.moved_on)
    return span


def _mover_seat(
    roster: dict[int, list[HouseRosterEntry]], positions: HousePositionsByLd, member_id: str
) -> tuple[int | None, str | None]:
    """``(LD, ballot seat)`` for the mover's own House row — the seat ``None`` when the
    ballot does not position them, both ``None`` when the row did not parse."""
    rows = {entry.member_id: (ld, entry) for ld, entries in roster.items() for entry in entries}
    if member_id not in rows:
        return None, None
    ld, entry = rows[member_id]
    qualifier = position_for(positions, ld, entry.folded_last, entry.party_slug)
    return ld, None if qualifier is None else house_span_discriminator(ld, qualifier)


def _continued_seat(move: ChamberMove, prior: Observation | None) -> str | None:
    """The previous biennium's seat, if the move biennium shares its map and LD."""
    if prior is None or move.biennium in REDISTRICTING_ERA_START_BIENNIA:
        return None
    if parse_house_span_discriminator(prior.discriminator)[0] != move.ld:
        return None
    return prior.discriminator


def _house_end(move: ChamberMove, seat: str, events: list[SuccessionEvent]) -> date:
    """The move, or a successor's earlier seating on ``seat`` inside the move biennium."""
    floor = biennium_start_date(move.biennium)
    successors = [
        e.effective_date
        for e in events
        if e.kind == KIND_SEATED
        and e.seat_kind == KIND_HOUSE
        and e.seat_discriminator == seat
        and e.member_id != move.member_id
        and floor < e.effective_date < move.moved_on
    ]
    return min(successors, default=move.moved_on)


def _house_ends(house_spans: Iterable[TenureSpan]) -> dict[tuple[str, str], date]:
    """``(member, biennium) → valid_to`` of the member's **last** House span closed
    strictly inside that biennium — a dated end, never the quantized floor or ceiling."""
    out: dict[tuple[str, str], date] = {}
    for span in house_spans:
        if span.kind != KIND_HOUSE or span.valid_to is None:
            continue
        floor = biennium_start_date(span.end_biennium)
        ceiling = date(floor.year + 1, 12, 31)
        key = (span.member_id, span.end_biennium)
        if floor < span.valid_to < ceiling and span.valid_to > out.get(key, floor):
            out[key] = span.valid_to
    return out


def _is_senate_seating(event: SuccessionEvent) -> bool:
    return event.kind == KIND_SEATED and event.seat_kind == KIND_SENATE


def _is_house_vacate(event: SuccessionEvent) -> bool:
    return event.kind == KIND_VACATED and event.seat_kind == KIND_HOUSE
