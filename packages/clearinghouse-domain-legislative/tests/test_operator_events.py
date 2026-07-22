"""OperatorEvent model round-trip + constraints (#107)."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from clearinghouse_domain_legislative.operator_events import (
    KIND_DEPARTED,
    KIND_SEATED,
    OperatorEvent,
    event_source_id,
)


def test_event_source_id_departed_omits_seat():
    sid = event_source_id("29091", KIND_DEPARTED, date(2025, 4, 19))
    assert sid == "29091:departed:2025-04-19"


def test_event_source_id_seated_keys_on_seat():
    sid = event_source_id(
        "35410",
        KIND_SEATED,
        date(2025, 6, 3),
        seat_kind="chamber-senate",
        seat_discriminator="5",
    )
    assert sid == "35410:seated:chamber-senate:5:2025-06-03"


async def test_departed_event_round_trips(db_session, usa_wa):
    row = OperatorEvent(
        source_id=event_source_id("29091", KIND_DEPARTED, date(2025, 4, 19)),
        member_id="29091",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://example.gov/ramos",
        entered_by="greg",
    )
    db_session.add(row)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(OperatorEvent).where(OperatorEvent.member_id == "29091"))
    ).scalar_one()
    assert fetched.kind == KIND_DEPARTED
    assert fetched.seat_kind is None
    assert fetched.source == "usa_wa_operator"
    assert fetched.superseded_by_id is None
    assert fetched.created_at is not None


async def test_seated_requires_seat_shape(db_session, usa_wa):
    """The seat-shape check constraint rejects a seated event with no seat."""
    bad = OperatorEvent(
        source_id="35410:seated:2025-06-03",
        member_id="35410",
        kind=KIND_SEATED,
        reason="appointed",
        effective_date=date(2025, 6, 3),
        evidence_url="https://example.gov/hunt",
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_supersedes_chain(db_session, usa_wa):
    original = OperatorEvent(
        source_id=event_source_id("29091", KIND_DEPARTED, date(2025, 4, 19)),
        member_id="29091",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, 19),
        evidence_url="https://example.gov/ramos",
    )
    db_session.add(original)
    await db_session.flush()

    correction = OperatorEvent(
        source_id=event_source_id("29091", KIND_DEPARTED, date(2025, 4, 20)),
        member_id="29091",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, 20),
        evidence_url="https://example.gov/ramos-official",
    )
    db_session.add(correction)
    await db_session.flush()
    original.superseded_by_id = correction.id
    await db_session.flush()

    current = (
        await db_session.execute(
            select(OperatorEvent).where(
                OperatorEvent.member_id == "29091",
                OperatorEvent.superseded_by_id.is_(None),
            )
        )
    ).scalar_one()
    assert current.effective_date == date(2025, 4, 20)
