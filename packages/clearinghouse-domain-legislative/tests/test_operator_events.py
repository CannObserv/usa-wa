"""OperatorEvent model round-trip + constraints (#107)."""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from clearinghouse_domain_legislative.operator_events import (
    KIND_DEPARTED,
    KIND_SEATED,
    OperatorEvent,
    current_clause,
    event_source_id,
)


def test_operator_events_live_in_the_registry_schema():
    """Curated human input sits beside ``registry.adjudications`` (#412 Q1), off the
    ``canonical`` schema PR F drops."""
    assert OperatorEvent.__table__.schema == "registry"


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


def _ramos(day: int = 19) -> OperatorEvent:
    return OperatorEvent(
        source_id=event_source_id("29091", KIND_DEPARTED, date(2025, 4, day)),
        member_id="29091",
        kind=KIND_DEPARTED,
        reason="died",
        effective_date=date(2025, 4, day),
        evidence_url="https://example.gov/ramos",
    )


async def test_a_retracted_row_is_not_current(db_session, usa_wa):
    """#468: an event can be wrong with no corrected event to replace it — a boundary
    projected onto a member who never crossed it. Retraction leaves the row (provenance
    is append-only) and takes it out of the current set every reader shares."""
    kept, retracted = _ramos(19), _ramos(20)
    retracted.retracted_at = datetime(2026, 10, 7, tzinfo=UTC)
    db_session.add_all([kept, retracted])
    await db_session.flush()

    current = (await db_session.execute(select(OperatorEvent).where(current_clause()))).scalars()
    assert [row.id for row in current] == [kept.id]


async def test_a_row_is_superseded_or_retracted_never_both(db_session, usa_wa):
    """A superseded row is already out of the current set, and a retraction of it would
    say nothing about the correction that stands in its place."""
    original, correction = _ramos(19), _ramos(20)
    db_session.add_all([original, correction])
    await db_session.flush()
    original.superseded_by_id = correction.id
    original.retracted_at = datetime(2026, 10, 7, tzinfo=UTC)
    with pytest.raises(IntegrityError):
        await db_session.flush()
