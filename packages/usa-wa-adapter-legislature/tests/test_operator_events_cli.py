"""Operator-event CLI (#107) — validation + record + supersede + batch."""

from datetime import date

import pytest
from sqlalchemy import select

from clearinghouse_domain_legislative.identity import Person
from clearinghouse_domain_legislative.operator_events import OperatorEvent
from usa_wa_adapter_legislature.operator_events import (
    EventSpec,
    OperatorEventError,
    load_specs,
    validate_and_record,
)
from usa_wa_adapter_legislature.operator_events_store import get_or_create_operator_source
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction


async def _source(session):
    return await get_or_create_operator_source(session, await resolve_jurisdiction(session))


async def _person(session, mid):
    session.add(Person(source="usa_wa_legislature", source_id=mid, name_full="M"))
    await session.flush()


def _departed(member="100", d=date(2025, 4, 19)):
    return EventSpec(
        member_id=member,
        kind="departed",
        reason="died",
        effective_date=d,
        evidence_url="https://example.gov/x",
    )


async def test_records_a_valid_departed_event(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    event = await validate_and_record(db_session, source, _departed())
    assert event.kind == "departed" and event.member_id == "100"


async def test_unknown_member_rejected(db_session, usa_wa):
    source = await _source(db_session)
    with pytest.raises(OperatorEventError, match="resolves to no"):
        await validate_and_record(db_session, source, _departed(member="999"))


async def test_bad_reason_for_kind_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    bad = EventSpec(
        member_id="100",
        kind="departed",
        reason="appointed",
        effective_date=date(2025, 4, 19),
        evidence_url="https://x",
    )
    with pytest.raises(OperatorEventError, match="reason"):
        await validate_and_record(db_session, source, bad)


async def test_seated_without_seat_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    bad = EventSpec(
        member_id="100",
        kind="seated",
        reason="appointed",
        effective_date=date(2025, 6, 3),
        evidence_url="https://x",
    )
    with pytest.raises(OperatorEventError, match="requires --seat"):
        await validate_and_record(db_session, source, bad)


async def test_departed_with_seat_rejected(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    bad = EventSpec(
        member_id="100",
        kind="departed",
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://x",
        seat_kind="chamber-senate",
        seat_discriminator="5",
    )
    with pytest.raises(OperatorEventError, match="must not carry a seat"):
        await validate_and_record(db_session, source, bad)


async def test_supersede_records_correction(db_session, usa_wa):
    await _person(db_session, "100")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _departed(d=date(2025, 4, 19)))
    corrected = await validate_and_record(
        db_session,
        source,
        EventSpec(
            member_id="100",
            kind="departed",
            reason="died",
            effective_date=date(2025, 4, 20),
            evidence_url="https://x",
            supersede_id=str(prior.id),
        ),
    )
    assert corrected.effective_date == date(2025, 4, 20)
    refreshed = (
        await db_session.execute(select(OperatorEvent).where(OperatorEvent.id == prior.id))
    ).scalar_one()
    assert refreshed.superseded_by_id == corrected.id


def test_load_specs_parses_batch():
    specs = load_specs(
        [
            {
                "member_id": "29091",
                "kind": "departed",
                "reason": "died",
                "effective_date": "2025-04-19",
                "evidence_url": "https://a",
            },
            {
                "member_id": "35410",
                "kind": "seated",
                "reason": "appointed",
                "effective_date": "2025-06-03",
                "evidence_url": "https://b",
                "seat_kind": "chamber-senate",
                "seat_discriminator": "5",
            },
        ]
    )
    assert [s.kind for s in specs] == ["departed", "seated"]
    assert specs[1].seat_discriminator == "5"


def test_load_specs_rejects_non_list():
    with pytest.raises(OperatorEventError, match="JSON array"):
        load_specs({"member_id": "1"})
