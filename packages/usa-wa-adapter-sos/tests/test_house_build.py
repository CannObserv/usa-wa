"""End-to-end WSL+SOS House Position span build (#101), fully offline.

The re-partition builder: WSL sponsor roster (who sits) + SOS results archive (the ballot
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
from usa_wa_adapter_sos.house.build import HouseSpanResult, build_house_position_spans

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, RawPayload, Source
from clearinghouse_domain_legislative.identity import Assignment, Person
from clearinghouse_domain_legislative.operator_events import KIND_VACATED
from usa_wa_adapter_legislature.adapter import committee_members_hist_resource_id
from usa_wa_adapter_legislature.operator_events_store import (
    get_or_create_operator_source,
    record_operator_event,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction

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
    header = '"Race","Candidate","Party"\r\n'
    body = "".join(
        f'"LEGISLATIVE DISTRICT {ld} - {race}","{ballot}","{party}"\r\n'
        for race, ld, ballot, party in rows
    )
    return (header + body).encode()


class _StubSponsorClient:
    async def fetch_sponsors(self, biennium):  # pragma: no cover
        raise AssertionError(f"live sponsor pull for {biennium}; roster must be archive-first")

    async def parse_sponsors(self, wire):
        return json.loads(wire.decode())


async def _sources(db_session, usa_wa):
    wsl = await _source(db_session, usa_wa, "usa_wa_legislature", "soap")
    sos = await _source(db_session, usa_wa, "usa_wa_sos_results", "rest")
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
        "sos-legresults:20221108",
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
        "sos-legresults:20161108",
        _sos_csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Democratic Party)")),
    )
    await _archive(
        db_session,
        sos,
        "sos-legresults:20181106",
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


async def test_pre_2009_seat_is_backchained_from_a_later_ballot_anchor(db_session, usa_wa):
    """#118: a member ballot-anchored in 2009-10 (the SOS floor) with continuous same-LD tenure
    back to 2007-08 gets their Position back-chained one biennium — building ONE deep span
    starting 2007-08, not a shallow 2009-10 one. The pre-floor biennium cites the sponsor roster
    (no ballot attests it) and surfaces as ``seeded`` in coverage."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)
    await _archive(db_session, wsl, "sponsors:2007-08", _sponsor_wire((100, 5, "Rivers", "House")))
    await _archive(db_session, wsl, "sponsors:2009-10", _sponsor_wire((100, 5, "Rivers", "House")))
    # Ballot exists only for the 2008 general (seats 2009-10); 2007-08 is below the SOS floor.
    await _archive(
        db_session,
        sos,
        "sos-legresults:20081104",
        _sos_csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Democratic Party)")),
    )

    result = await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium="2009-10"
    )

    assert result.house_spans == 1
    assert result.coverage["2007-08"]["seeded"] == 1
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source == "usa_wa_legislature")
        )
    ).scalar_one()
    # ONE deep span from the back-chained 2007-08 start, reaching the current 2009-10 (open).
    assert row.source_id == "100:chamber-house:ld-5-position-1:2007-08"
    assert row.valid_from == date(2007, 1, 1) and row.is_active is True
    # The 2007-08 (pre-floor, back-chained) biennium cites the sponsor roster; 2009-10 the ballot.
    cited = {
        (
            await db_session.execute(
                select(FetchEvent.resource_id).where(FetchEvent.id == cite.fetch_event_id)
            )
        ).scalar_one()
        for cite in (await db_session.execute(select(Citation).where(Citation.entity_id == row.id)))
        .scalars()
        .all()
    }
    assert cited == {"sponsors:2007-08", "sos-legresults:20081104"}


async def test_backchained_pre_floor_span_survives_the_restricted_daily_redrive(db_session, usa_wa):
    """#118 + the #100-CR invariant: the daily restricted re-drive (restrict_to_biennium=current)
    must back-chain a *current* member's pre-floor span to the SAME deep start as the unrestricted
    backfill — not a shallow current-only one. This is the property the deploy-orphan safety rests
    on; a regression that skipped back-chain when restricted would re-arm it."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)
    await _archive(db_session, wsl, "sponsors:2007-08", _sponsor_wire((100, 5, "Rivers", "House")))
    await _archive(db_session, wsl, "sponsors:2009-10", _sponsor_wire((100, 5, "Rivers", "House")))
    await _archive(
        db_session,
        sos,
        "sos-legresults:20081104",
        _sos_csv(("State Representative Pos. 1", 5, "Ann Rivers", "(Prefers Democratic Party)")),
    )

    result = await build_house_position_spans(
        db_session,
        sponsor_client=_StubSponsorClient(),
        current_biennium="2009-10",
        restrict_to_biennium="2009-10",  # the daily path
    )

    assert result.house_spans == 1
    row = (
        await db_session.execute(
            select(Assignment).where(Assignment.source == "usa_wa_legislature")
        )
    ).scalar_one()
    # Deep back-chained start — identical to the unrestricted backfill, not ld-...:2009-10.
    assert row.source_id == "100:chamber-house:ld-5-position-1:2007-08"
    assert row.valid_from == date(2007, 1, 1) and row.is_active is True


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


async def test_appointee_is_seated_by_elimination_and_cites_the_roster(db_session, usa_wa):
    """#103 end-to-end: a mid-biennium appointee absent from the SOS ballot is seated by
    within-LD elimination (the LD's other member is ballot-matched, so the remaining position is
    theirs), and their span cites the WSL sponsor roster — the wire that names them — while the
    ballot-matched member keeps citing the SOS cohort. Coverage surfaces the inference."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 33)
    await _add_person(db_session, 100)
    await _add_person(db_session, 101)
    await _archive(
        db_session,
        wsl,
        "sponsors:2025-26",
        _sponsor_wire((100, 33, "Gregerson", "House"), (101, 33, "Obras", "House")),
    )
    # The 2024 ballot: Gregerson won Pos 2; Pos 1's winner (Orwall) departed and is blanked out
    # of the roster; her appointed successor (Obras) is on no ballot line.
    await _archive(
        db_session,
        sos,
        "sos-legresults:20241105",
        _sos_csv(
            ("State Representative Pos. 1", 33, "Tina Orwall", "(Prefers Democratic Party)"),
            ("State Representative Pos. 2", 33, "Mia Gregerson", "(Prefers Democratic Party)"),
        ),
    )

    result = await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.house_spans == 2
    assert result.coverage[CURRENT]["inferred"] == 1

    async def _cited_resource(source_id):
        row = (
            await db_session.execute(select(Assignment).where(Assignment.source_id == source_id))
        ).scalar_one()
        cite = (
            await db_session.execute(select(Citation).where(Citation.entity_id == row.id))
        ).scalar_one()
        ev = (
            await db_session.execute(select(FetchEvent).where(FetchEvent.id == cite.fetch_event_id))
        ).scalar_one()
        return ev.resource_id

    inferred = await _cited_resource("101:chamber-house:ld-33-position-1:2025-26")
    assert inferred == "sponsors:2025-26"  # the wire that names the appointee (#103)
    matched = await _cited_resource("100:chamber-house:ld-33-position-2:2025-26")
    assert matched == "sos-legresults:20241105"


async def test_odd_year_special_seats_appointee_by_ballot_and_cites_the_odd_cohort(
    db_session, usa_wa
):
    """#123 §1 end-to-end: a mid-biennium appointee (Obras, LD33 P1, appointed 2025) who won the
    **odd-year** Nov-2025 special is seated by that ballot — matched, not #103-inferred — and cites
    the odd cohort ``sos-legresults:20251104``, while the even-cohort holder (Gregerson P2) keeps
    citing ``sos-legresults:20241105``. Before #123 the odd cohort was archived but never joined,
    so Obras was elimination-inferred (roster-cited); now the ballot fact seats her directly."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 33)
    await _add_person(db_session, 100)  # Gregerson (won Pos 2, 2024)
    await _add_person(db_session, 101)  # Obras (appointed; won the 2025 special, Pos 1)
    await _archive(
        db_session,
        wsl,
        "sponsors:2025-26",
        _sponsor_wire((100, 33, "Gregerson", "House"), (101, 33, "Obras", "House")),
    )
    # Even seating cohort (2024): Orwall won Pos 1 (since departed, off roster); Gregerson Pos 2.
    await _archive(
        db_session,
        sos,
        "sos-legresults:20241105",
        _sos_csv(
            ("State Representative Pos. 1", 33, "Tina Orwall", "(Prefers Democratic Party)"),
            ("State Representative Pos. 2", 33, "Mia Gregerson", "(Prefers Democratic Party)"),
        ),
    )
    # Odd-year special (Nov 2025): Obras won the LD33 Pos 1 unexpired term.
    await _archive(
        db_session,
        sos,
        "sos-legresults:20251104",
        _sos_csv(
            ("State Representative Pos. 1", 33, "Chelsea Obras", "(Prefers Democratic Party)"),
        ),
    )

    result = await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.house_spans == 2
    # Obras is now a ballot match, not an inference (#123 goal — the inferred count drops).
    assert result.coverage[CURRENT]["inferred"] == 0
    assert result.coverage[CURRENT]["matched"] == 2

    async def _cited_resource(source_id):
        row = (
            await db_session.execute(select(Assignment).where(Assignment.source_id == source_id))
        ).scalar_one()
        cite = (
            await db_session.execute(select(Citation).where(Citation.entity_id == row.id))
        ).scalar_one()
        ev = (
            await db_session.execute(select(FetchEvent).where(FetchEvent.id == cite.fetch_event_id))
        ).scalar_one()
        return ev.resource_id

    # Obras' seat cites the odd special wire that seated her; Gregerson cites the even seating wire.
    assert (
        await _cited_resource("101:chamber-house:ld-33-position-1:2025-26")
        == "sos-legresults:20251104"
    )
    assert (
        await _cited_resource("100:chamber-house:ld-33-position-2:2025-26")
        == "sos-legresults:20241105"
    )


async def test_odd_sourced_seat_cites_the_odd_cohort_despite_a_shared_even_surname(
    db_session, usa_wa
):
    """#123 CR finding-1: the odd-cohort citation must route on the member's *merged* resolution,
    not a bare odd-cohort hit. Here the even seating cohort carries a losing ``Smith`` (Pos 2, R)
    while the odd special winner is a *different* ``Smith`` (Pos 1, D, the appointee). A naive
    ``odd_hit is not None``/``even_hit is None`` test would see the even loser resolve the surname
    to Pos 2 and mis-cite the appointee's Pos 1 seat to the even wire. The merged-resolution test
    routes it correctly to the odd cohort."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 100)  # Vale (even Pos 2 winner, sitting)
    await _add_person(db_session, 101)  # Ann Smith (odd Pos 1 winner, appointee)
    await _archive(
        db_session,
        wsl,
        "sponsors:2025-26",
        _sponsor_wire((100, 5, "Vale", "House"), (101, 5, "Smith", "House")),
    )
    # Even 2024: Green won Pos 1 (since departed, off the roster); Vale won Pos 2; a *losing*
    # Bob Smith (R) also ran for Pos 2 — sharing the odd winner's surname at a different position.
    await _archive(
        db_session,
        sos,
        "sos-legresults:20241105",
        _sos_csv(
            ("State Representative Pos. 1", 5, "Al Green", "(Prefers Democratic Party)"),
            ("State Representative Pos. 2", 5, "Cy Vale", "(Prefers Democratic Party)"),
            ("State Representative Pos. 2", 5, "Bob Smith", "(Prefers Republican Party)"),
        ),
    )
    # Odd 2025 special: Ann Smith (D) won Pos 1 (the seat Green vacated).
    await _archive(
        db_session,
        sos,
        "sos-legresults:20251104",
        _sos_csv(("State Representative Pos. 1", 5, "Ann Smith", "(Prefers Democratic Party)")),
    )

    result = await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.house_spans == 2
    assert result.coverage[CURRENT]["matched"] == 2  # both ballot-matched via the merged map

    async def _cited_resource(source_id):
        row = (
            await db_session.execute(select(Assignment).where(Assignment.source_id == source_id))
        ).scalar_one()
        cite = (
            await db_session.execute(select(Citation).where(Citation.entity_id == row.id))
        ).scalar_one()
        ev = (
            await db_session.execute(select(FetchEvent).where(FetchEvent.id == cite.fetch_event_id))
        ).scalar_one()
        return ev.resource_id

    # The appointee's Pos 1 seat cites the odd wire that seated her — NOT the even cohort whose
    # losing Bob Smith shares her surname.
    assert (
        await _cited_resource("101:chamber-house:ld-5-position-1:2025-26")
        == "sos-legresults:20251104"
    )
    assert (
        await _cited_resource("100:chamber-house:ld-5-position-2:2025-26")
        == "sos-legresults:20241105"
    )


async def test_historical_odd_special_only_winner_is_seated_in_an_unrestricted_backfill(
    db_session, usa_wa
):
    """#123 regression — the LD30/Hickel shape: a *pure* odd-year special winner in a
    **non-current** biennium (no even-year ballot win to #118-back-chain from) is seated by the
    odd-merge in an unrestricted (historical backfill) build.

    The existing #123 tests all seat a *current-biennium* appointee (Obras 2025), which the daily
    refresh materializes. But the daily refresh runs ``restrict_to_biennium=current`` and never
    re-emits an old biennium, so a historical odd-special-only winner materializes **only** when the
    backfill runs — and the merge must seat her there. Here Hickel (LD30 Pos 2, won only the 2015
    special) has no even-year anchor; the even seating cohort names a since-departed Pos-2 holder
    (Freeman, off the roster), so the odd special is her sole seat source. Regression guard for the
    stale-backfill gap that left LD30 Pos 2 2015-16 unfilled after #123 landed."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 30)
    await _add_person(db_session, 200)  # Kochmar (won Pos 1, 2014 even)
    await _add_person(db_session, 201)  # Hickel (won only the 2015 odd special, Pos 2)
    # Historical biennium only; current is 2025-26, so this exercises the non-current backfill path.
    await _archive(
        db_session,
        wsl,
        "sponsors:2015-16",
        _sponsor_wire((200, 30, "Kochmar", "House"), (201, 30, "Hickel", "House")),
    )
    # Even 2014: Kochmar Pos 1; Freeman won Pos 2 but died end of 2014 (off the 2015-16 roster).
    await _archive(
        db_session,
        sos,
        "sos-legresults:20141104",
        _sos_csv(
            ("State Representative Pos. 1", 30, "Linda Kochmar", "(Prefers Republican Party)"),
            ("State Representative Pos. 2", 30, "Roger Freeman", "(Prefers Democratic Party)"),
        ),
    )
    # Odd 2015 special: Hickel won the LD30 Pos 2 unexpired term.
    await _archive(
        db_session,
        sos,
        "sos-legresults:20151103",
        _sos_csv(("State Representative Pos. 2", 30, "Teri Hickel", "(Prefers Republican Party)")),
    )

    result = await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    # Both LD30 seats materialize in the backfill: Kochmar (even Pos 1) + Hickel (odd Pos 2).
    assert result.house_spans == 2
    assert result.coverage["2015-16"]["matched"] == 2
    hickel = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.source_id == "201:chamber-house:ld-30-position-2:2015-16"
            )
        )
    ).scalar_one()
    assert hickel.valid_from == date(2015, 1, 1)
    assert hickel.valid_to == date(2016, 12, 31)
    # Cited to the odd special wire that actually seated her — not the even seating cohort.
    cite = (
        await db_session.execute(select(Citation).where(Citation.entity_id == hickel.id))
    ).scalar_one()
    ev = (
        await db_session.execute(select(FetchEvent).where(FetchEvent.id == cite.fetch_event_id))
    ).scalar_one()
    assert ev.resource_id == "sos-legresults:20151103"


async def test_operator_vacated_closes_a_mover_house_seat_at_the_move_date(db_session, usa_wa):
    """#107: a House→Senate mover (Hunt-shaped) is normally mover-excluded, but an operator
    `vacated` event keeps her House row (keep_ids) so the span builds, then closes it at her real
    chamber-move date — with a field-level operator citation. Her party/Senate are the sponsor
    builder's concern; here only the House seat is affected."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 5)
    await _add_person(db_session, 35410)
    # Same Id in House and Senate rows = a mid-biennium mover (would be mover-excluded).
    await _archive(
        db_session,
        wsl,
        "sponsors:2025-26",
        _sponsor_wire((35410, 5, "Hunt", "House"), (35410, 5, "Hunt", "Senate")),
    )
    await _archive(
        db_session,
        sos,
        "sos-legresults:20241105",
        _sos_csv(("State Representative Pos. 1", 5, "Victoria Hunt", "(Prefers Democratic Party)")),
    )
    juris = await resolve_jurisdiction(db_session)
    op_source = await get_or_create_operator_source(db_session, juris)
    await record_operator_event(
        db_session,
        op_source,
        member_id="35410",
        kind=KIND_VACATED,
        reason="moved",
        effective_date=date(2025, 6, 3),
        evidence_url="https://example.gov/hunt",
        seat_kind="chamber-house",
        seat_discriminator="ld-5-position-1",
    )

    await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    row = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.source_id == "35410:chamber-house:ld-5-position-1:2025-26"
            )
        )
    ).scalar_one()
    assert row.valid_to == date(2025, 6, 3) and row.is_active is False
    field_cites = await db_session.scalar(
        select(func.count())
        .select_from(Citation)
        .where(Citation.entity_id == row.id, Citation.field_path == "valid_to")
    )
    assert field_cites == 1


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
        "sos-legresults:20221108",
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
        "sos-legresults:20241105",
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


class _StubMemberClient:
    """Committee roster per wire: ``b"<r:100,200/>"`` names member ids."""

    async def parse_historical_committee_members(self, wire):
        ids = wire.decode().removeprefix("<r:").removesuffix("/>")
        return [{"Id": int(i), "FirstName": "A", "LastName": "B"} for i in ids.split(",") if i]


async def _archive_committee_roster(db_session, wsl, biennium, wire):
    resource_id = committee_members_hist_resource_id(biennium, "888", "House", "Appropriations")
    await _archive(db_session, wsl, resource_id, wire)


async def test_mover_house_row_is_excluded_and_appointee_seated(db_session, usa_wa):
    """#105 (a) end-to-end: the Alvarado shape. A mid-biennium House→Senate mover keeps a named
    House row (same Id as their Senate row) that still ballot-matches their old position — the
    LD reads 3-member and the #103 elimination declines, leaving the appointed replacement
    unseated and the mover's House span open. With the same-wire Id exclusion the LD reads
    2-member: the appointee is seated by elimination and the mover's House span closes."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 34)
    for mid in (100, 200, 300):
        await _add_person(db_session, mid)
    await _archive(
        db_session,
        wsl,
        "sponsors:2023-24",
        _sponsor_wire((100, 34, "Alvarado", "House"), (200, 34, "Fitzgibbon", "House")),
    )
    await _archive(
        db_session,
        sos,
        "sos-legresults:20221108",
        _sos_csv(
            ("State Representative Pos. 1", 34, "Emily Alvarado", "(Prefers Democratic Party)"),
            ("State Representative Pos. 2", 34, "Joe Fitzgibbon", "(Prefers Democratic Party)"),
        ),
    )
    # 2025-26: Alvarado won Pos 1 on the 2024 ballot, then moved to the LD's Senate seat; the
    # wire carries BOTH her rows (same Id) + her appointed replacement (Thomas, no ballot line).
    await _archive(
        db_session,
        wsl,
        "sponsors:2025-26",
        _sponsor_wire(
            (100, 34, "Alvarado", "House"),
            (100, 34, "Alvarado", "Senate"),
            (200, 34, "Fitzgibbon", "House"),
            (300, 34, "Thomas", "House"),
        ),
    )
    await _archive(
        db_session,
        sos,
        "sos-legresults:20241105",
        _sos_csv(
            ("State Representative Pos. 1", 34, "Emily Alvarado", "(Prefers Democratic Party)"),
            ("State Representative Pos. 2", 34, "Joe Fitzgibbon", "(Prefers Democratic Party)"),
        ),
    )

    result = await build_house_position_spans(
        db_session, sponsor_client=_StubSponsorClient(), current_biennium=CURRENT
    )

    assert result.coverage[CURRENT]["inferred"] == 1
    # The appointee holds Pos 1 (inferred), open.
    appointee = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.source_id == "300:chamber-house:ld-34-position-1:2025-26"
            )
        )
    ).scalar_one()
    assert appointee.is_active is True
    # The mover's House span ends at the prior biennium — not open alongside their Senate seat.
    mover = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.source_id == "100:chamber-house:ld-34-position-1:2023-24"
            )
        )
    ).scalar_one()
    assert mover.is_active is False and mover.valid_to == date(2024, 12, 31)


async def test_stale_named_row_is_excluded_and_appointee_seated(db_session, usa_wa):
    """#105 (b) end-to-end: the Senn shape. A resigned member stays fully named AND
    ballot-matched (she won the seating election), blocking the elimination — the
    committee-corroborated exclusion drops her, the LD reads 2-member, and the appointed
    replacement (committee-active, no ballot line) is seated."""
    wsl, sos = await _sources(db_session, usa_wa)
    await _add_ld(db_session, usa_wa, 41)
    for mid in (100, 200, 300):
        await _add_person(db_session, mid)
    await _archive(
        db_session,
        wsl,
        "sponsors:2025-26",
        _sponsor_wire(
            (100, 41, "Senn", "House"),
            (200, 41, "Thai", "House"),
            (300, 41, "Zahn", "House"),
        ),
    )
    # Committee rosters name the sitting members (Thai, Zahn) — not Senn.
    await _archive_committee_roster(db_session, wsl, CURRENT, b"<r:200,300/>")
    await _archive(
        db_session,
        sos,
        "sos-legresults:20241105",
        _sos_csv(
            ("State Representative Pos. 1", 41, "Tana Senn", "(Prefers Democratic Party)"),
            ("State Representative Pos. 2", 41, "My-Linh Thai", "(Prefers Democratic Party)"),
        ),
    )

    result = await build_house_position_spans(
        db_session,
        sponsor_client=_StubSponsorClient(),
        member_client=_StubMemberClient(),
        current_biennium=CURRENT,
        stale_min_coverage=0.5,
    )

    assert result.coverage[CURRENT]["members"] == 2  # Senn excluded pre-projection
    assert result.coverage[CURRENT]["inferred"] == 1
    zahn = (
        await db_session.execute(
            select(Assignment).where(
                Assignment.source_id == "300:chamber-house:ld-41-position-1:2025-26"
            )
        )
    ).scalar_one()
    assert zahn.is_active is True
    # Senn gets no seat at all this biennium.
    senn = (
        (await db_session.execute(select(Assignment).where(Assignment.source_id.like("100:%"))))
        .scalars()
        .all()
    )
    assert senn == []
