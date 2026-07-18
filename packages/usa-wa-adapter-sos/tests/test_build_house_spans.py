"""End-to-end WSL+SOS House Position span build (#101), fully offline.

The re-partition builder: WSL sponsor roster (who sits) + SOS filing archive (the ballot
Position) → merged ``state_representative`` Position seat **spans**, ``usa_wa_legislature``-sourced
(symmetric with the Senate seat). One builder drives the daily re-drive AND the historical
backfill, so a member serving across the 2018 boundary builds ONE deep span whether restricted
or not — the finding-1 defect (#100 CR) cannot recur.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from ulid import ULID as _ULID
from usa_wa_adapter_sos.build_house_spans import HouseSpanResult, build_house_position_spans

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person

CURRENT = "2025-26"


async def _source(session, usa_wa, slug, kind):
    row = Source(jurisdiction_id=usa_wa.id, name=slug, slug=slug, kind=kind)
    session.add(row)
    await session.flush()
    return row


async def _add_ld(session, usa_wa, n):
    session.add(
        Jurisdiction(
            slug=f"usa-wa-ld-{n}",
            name=f"LD {n}",
            type_id=usa_wa.type_id,
            pm_jurisdiction_id=_ULID(),
            recorded_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _add_person(session, mid):
    session.add(Person(source="usa_wa_legislature", source_id=str(mid), name_full=f"M{mid}"))
    await session.flush()


async def _archive(session, source, resource_id, body):
    ev = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes([hash(resource_id) & 0xFF]) * 32,
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(RawPayload(fetch_event_id=ev.id, content_type="x", body=body, size_bytes=len(body)))
    await session.flush()


def _sponsor_wire(*rows):
    return json.dumps(
        [
            {
                "Id": mid,
                "FirstName": "M",
                "LastName": last,
                "District": str(ld),
                "Agency": ag,
                "Party": "D",
            }
            for mid, ld, last, ag in rows
        ]
    ).encode()


def _sos_csv(*rows):
    header = "RaceName,RaceJurisdictionName,BallotName,PartyName\r\n"
    body = "".join(
        f"{race},Legislative District {ld},{ballot},{party}\r\n" for race, ld, ballot, party in rows
    )
    return (header + body).encode()


class _StubSponsorClient:
    async def fetch_sponsors(self, biennium):  # pragma: no cover
        raise AssertionError(f"live sponsor pull for {biennium}; roster must be archive-first")

    async def parse_sponsors(self, wire):
        return json.loads(wire.decode())


async def _sources(db_session, usa_wa):
    wsl = await _source(db_session, usa_wa, "usa_wa_legislature", "soap")
    sos = await _source(db_session, usa_wa, "usa_wa_sos", "rest")
    return wsl, sos


async def test_house_seat_is_legislature_sourced_on_seat_role(db_session, usa_wa):
    """A member seated LD5 Pos1 with an SOS filing → one usa_wa_legislature Assignment on the
    state_representative seat Role, citing the SOS cohort."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)
    await _archive(db_session, wsl, "sponsors:2023-24", _sponsor_wire((100, 5, "Rivers", "House")))
    await _archive(
        db_session,
        sos,
        "sos-whofiled:202211",
        _sos_csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Democratic Party)")),
    )

    result = await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium="2023-24"
    )

    assert result.house_spans == 1
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source == "usa_wa_legislature")
        )
    ).scalar_one()
    assert row.source_id == "100:chamber-house:ld-5-position-1:2023-24"
    assert row.valid_from == date(2023, 1, 1) and row.valid_to is None and row.is_active is True
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Citation).where(Citation.entity_id == row.id)
        )
        == 1
    )


async def test_cross_2018_member_builds_one_deep_open_span_even_when_restricted(db_session, usa_wa):
    """The finding-1 property: a member serving 2017-18 → 2019-20 (across the boundary) builds
    ONE deep span starting 2017-18. The daily restricted re-drive (restrict_to_biennium=current)
    produces the same deep span — not a shallow current-only one — because it is the SAME builder
    with the SAME SOS positions."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)
    # Two consecutive bienniums spanning the 2018 boundary; SOS positions in both eras.
    await _archive(db_session, wsl, "sponsors:2017-18", _sponsor_wire((100, 5, "Rivers", "House")))
    await _archive(db_session, wsl, "sponsors:2019-20", _sponsor_wire((100, 5, "Rivers", "House")))
    await _archive(
        db_session,
        sos,
        "sos-whofiled:201611",
        _sos_csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Democratic Party)")),
    )
    await _archive(
        db_session,
        sos,
        "sos-whofiled:201811",
        _sos_csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Democratic Party)")),
    )

    # Daily restricted re-drive with current=2019-20 (the member's latest served biennium).
    result = await build_house_position_spans(
        db_session,
        sponsor_client=_StubSponsorClient(),
        current_biennium="2019-20",
        restrict_to_biennium="2019-20",
    )

    assert result.house_spans == 1
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source == "usa_wa_legislature")
        )
    ).scalar_one()
    # Deep: starts at the 2017-18 tenure start, open (reaches current 2019-20). NOT ld-...:2019-20.
    assert row.source_id == "100:chamber-house:ld-5-position-1:2017-18"
    assert row.valid_from == date(2017, 1, 1)
    assert row.valid_to is None and row.is_active is True
    # Cited every covered biennium (both SOS cohorts).
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Citation).where(Citation.entity_id == row.id)
        )
        == 2
    )


async def test_member_without_position_gets_no_seat(db_session, usa_wa):
    """A sitting House member with no SOS filing → no House Position seat (OQ1: emit nothing)."""
    wsl, _sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 9)
    await _add_person(db_session, 200)
    await _archive(db_session, wsl, "sponsors:2023-24", _sponsor_wire((200, 9, "Jones", "House")))
    # No SOS archive at all.

    result = await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium="2023-24"
    )

    assert result.house_spans == 0
    assert (await db_session.execute(select(func.count()).select_from(Assignment))).scalar() == 0


async def test_departed_member_open_span_is_closed_by_the_sweep(db_session, usa_wa):
    """#83, House: a member who departed at the boundary keeps no observation in the restricted
    rebuild → their open chamber-house span closes at the prior biennium end."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 5)
    await _add_ld(db_session, usa_wa, 9)
    await _add_person(db_session, 100)
    await _add_person(db_session, 200)
    await _archive(
        db_session,
        wsl,
        "sponsors:2023-24",
        _sponsor_wire((100, 5, "Rivers", "House"), (200, 9, "Jones", "House")),
    )
    await _archive(
        db_session,
        sos,
        "sos-whofiled:202211",
        _sos_csv(
            ("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Democratic Party)"),
            ("State Representative Pos. 1", 9, "Ann Jones", "(Prefers Democratic Party)"),
        ),
    )
    # Sitting-era build: both open.
    await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium="2023-24"
    )
    departed = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.source_id == "200:chamber-house:ld-9-position-1:2023-24"
            )
        )
    ).scalar_one()
    assert departed.is_active is True

    # 2025-26: only 100 re-elected; 200 departed. Daily restricted re-drive.
    await _archive(db_session, wsl, "sponsors:2025-26", _sponsor_wire((100, 5, "Rivers", "House")))
    await _archive(
        db_session,
        sos,
        "sos-whofiled:202411",
        _sos_csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Democratic Party)")),
    )
    result = await build_house_position_spans(
        db_session,
        sponsor_client=_StubSponsorClient(),
        current_biennium=CURRENT,
        restrict_to_biennium=CURRENT,
    )

    assert result.closed_stale == 1
    assert departed.is_active is False and departed.valid_to == date(2024, 12, 31)
    assert isinstance(result, HouseSpanResult)
