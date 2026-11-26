"""Member-elects (#600, spec § 2): elected to a seat, not yet seated.

From the seating general until the day before the term it was won for opens, the winner holds
a **member-elect** assignment, its own role beside (never instead of) any seat they hold now:
``chamber-house-elect`` on ``seat-elect:house:ld-N:position-P``, ``chamber-senate-elect`` on
``seat-elect:senate:ld-N``.

**Who.** The seating general's winners, by the SOS normalizers' own never-guess rule (a tie or
an unrankable count drops the seat): the House per ``(LD, Position)``, the Senate only for the
LDs whose seat was on that ballot, which leaves holdovers out by construction. A winner becomes
a person through the **next** biennium's WSL listing, joined the way the House seat already is
(``position_for``: LD, folded surname, the party tiebreak). A winner WSL hasn't listed has no
person yet and gets no member-elect until it does. Two listed members on one won seat is never
guessed: the seat is dropped and logged.

**When.** ``valid_from`` is general election day, ``dated``; the race's call lies between it
and ``valid_from_latest``, the first evidence: WSL's first archived listing
(``stg_wsl_first_seen``) or certification (``stg_sos_election``), whichever is earlier, clamped
to the window. ``valid_to`` is the day before the term opens (RCW 44.04.021), ``dated``: the
statute states it. An operator ``departed`` inside the window ends it there. ``is_active`` is
false throughout: a member-elect isn't serving.

**Since.** The 2026 general (2027-28) forward (§ 2.5): earlier ballots have no first-evidence
wires, and a window with nothing in it would be invented. Odd-year specials fill unexpired terms
from certification, not January, and wait for #605.

Built from the inputs **before** :func:`~usa_wa_pipeline.conformed.spans.
without_future_bienniums`, which drops the next biennium's listing until the rollover.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.boundary_basis import BASIS_DATED
from clearinghouse_domain_legislative.operator_events import KIND_DEPARTED
from clearinghouse_domain_legislative.operator_overlay import from_rows
from clearinghouse_domain_legislative.span_kinds import KIND_HOUSE_ELECT, KIND_SENATE_ELECT
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from clearinghouse_domain_legislative.terms import parse_biennium, previous_biennium
from usa_wa_adapter_sos.results.normalize import build_house_winners, build_senate_winners
from usa_wa_common.ballot import position_for
from usa_wa_common.elections import general_election_day
from usa_wa_common.legislative_calendar import WA_TERMS
from usa_wa_common.names import fold_token
from usa_wa_common.parties import canonicalize_party
from usa_wa_common.seats import district_number, house_span_discriminator
from usa_wa_facts_seats.house.roster import build_house_roster
from usa_wa_pipeline.conformed.house import sos_result_wires
from usa_wa_pipeline.conformed.wire import sponsor_wire_rows

logger = get_logger(__name__)

#: The first biennium with member-elects: the 2026 general's (§ 2.5).
ELECT_FLOOR = 2027


def build_elect_spans(
    *,
    sponsors: list[dict[str, Any]],
    sos_results: list[dict[str, Any]],
    first_seen: list[dict[str, Any]],
    elections: list[dict[str, Any]],
    events: Iterable[object] = (),
    counts: Counter | None = None,
) -> list[TenureSpan]:
    """Every member-elect span the staged inputs evidence, ordered like the other families.

    ``counts`` (a caller that reports passes one) receives, per build: ``won`` seats on the
    seating ballots, ``built`` member-elects, and the seats that built none — ``unlisted``
    (WSL lists no matching member yet), ``ambiguous`` (more than one) and ``no_evidence``."""
    tally: Counter = Counter() if counts is None else counts
    wires = sos_result_wires(sos_results)
    seen = _first_seen(first_seen)
    departed = _departures(events)
    spans: list[TenureSpan] = []
    for biennium, rows in sorted(sponsor_wire_rows(sponsors).items()):
        start = _start_year(biennium)
        if start is None or start < ELECT_FLOOR:
            continue
        wire = wires.get(start - 1)
        if not wire:
            logger.info("elect_no_seating_ballot", extra={"biennium": biennium})
            continue
        day = general_election_day(start - 1)
        certified = _certified(elections, day)
        eve = WA_TERMS.end(previous_biennium(biennium))
        won, winners, ambiguous = _winners(biennium, rows, wire)
        tally["won"] += won
        tally["ambiguous"] += ambiguous
        tally["unlisted"] += won - len(winners) - ambiguous
        for kind, discriminator, member_id in winners:
            evidence = [d for d in (seen.get((biennium, member_id)), certified) if d]
            if not evidence:
                tally["no_evidence"] += 1
                logger.warning(
                    "elect_no_evidence",
                    extra={"biennium": biennium, "member_id": member_id, "seat": discriminator},
                )
                continue
            end = min([eve, *(d for d in departed.get(member_id, []) if day < d < eve)])
            latest = min(max(min(evidence), day), end)
            tally["built"] += 1
            spans.append(
                TenureSpan(
                    member_id=member_id,
                    kind=kind,
                    discriminator=discriminator,
                    start_biennium=biennium,
                    end_biennium=biennium,
                    valid_from=day,
                    valid_to=end,
                    is_active=False,
                    start_basis=BASIS_DATED,
                    end_basis=BASIS_DATED,
                    start_window_end=latest if latest != day else None,
                    terms=WA_TERMS,
                )
            )
    spans.sort(key=lambda s: (s.member_id, s.kind, s.discriminator, s.start_biennium))
    return spans


def _winners(
    biennium: str, rows: list[dict[str, Any]], wire: list[dict[str, Any]]
) -> tuple[int, list[tuple[str, str, str]], int]:
    """``(won, winners, ambiguous)``: how many seats the ballot decided, ``(kind,
    discriminator, member_id)`` for each one exactly one listed member holds, and how many
    more than one claims (never guessed)."""
    claims: dict[tuple[str, str], list[str]] = defaultdict(list)
    house = build_house_winners(wire)
    for ld, entries in build_house_roster(rows).items():
        for entry in entries:
            qualifier = position_for(house, ld, entry.folded_last, entry.party_slug)
            if qualifier is not None:
                claims[(KIND_HOUSE_ELECT, house_span_discriminator(ld, qualifier))].append(
                    entry.member_id
                )
    senate = build_senate_winners(wire)
    named: dict[int, list[tuple[str, str | None]]] = defaultdict(list)
    for row in rows:
        ld = district_number(row.get("District"))
        last = (row.get("LastName") or "").strip()
        if row.get("Agency") != "Senate" or ld not in senate or not last:
            continue
        if fold_token(last) in senate[ld].name_keys:
            named[ld].append((str(row["Id"]), canonicalize_party(row.get("Party"))))
    for ld, members in named.items():
        # party breaks a tie, as position_for does for the House; it never refuses the one
        # listed senator the winner's ballot name carries (CR 1)
        if len({m for m, _ in members}) > 1 and senate[ld].party_slug is not None:
            members = [(m, p) for m, p in members if p == senate[ld].party_slug] or members
        claims[(KIND_SENATE_ELECT, str(ld))].extend(m for m, _ in members)
    out = []
    ambiguous = 0
    for (kind, discriminator), members in sorted(claims.items()):
        if len(set(members)) != 1:
            ambiguous += 1
            logger.warning(
                "elect_seat_ambiguous",
                extra={"biennium": biennium, "seat": discriminator, "members": sorted(members)},
            )
            continue
        out.append((kind, discriminator, members[0]))
    won = sum(len(positions) for positions in house.values()) + len(senate)
    return won, out, ambiguous


def _start_year(biennium: str) -> int | None:
    try:
        return parse_biennium(biennium)[0]
    except ValueError:
        return None


def _day(timestamp: Any) -> date | None:
    """A manifest timestamp's UTC day; ``None`` for a missing one."""
    if not timestamp:
        return None
    return datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).date()


def _first_seen(rows: list[dict[str, Any]]) -> dict[tuple[str, str], date]:
    out: dict[tuple[str, str], date] = {}
    for row in rows:
        day = _day(row.get("first_seen_at"))
        if day is not None:
            out[(str(row["biennium"]), str(row["member_id"]))] = day
    return out


def _certified(rows: list[dict[str, Any]], day: date) -> date | None:
    """The first archived election JSON for ``day``'s general that reads official."""
    stamp = day.strftime("%Y%m%d")
    days = [
        _day(row.get("fetched_at"))
        for row in rows
        # str(): a dbt frame hands back numpy.bool_ (never `is True`) or NaN for a null
        if str(row.get("election_date")) == stamp and str(row.get("is_official_results")) == "True"
    ]
    return min((d for d in days if d), default=None)


def _departures(events: Iterable[object]) -> dict[str, list[date]]:
    out: dict[str, list[date]] = defaultdict(list)
    for event in from_rows(events):
        if event.kind == KIND_DEPARTED:
            out[event.member_id].append(event.effective_date)
    return out
