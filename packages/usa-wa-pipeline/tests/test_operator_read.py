"""The curated operator-event read seam (#309).

Operator events are the span transform's one non-raw-store input — human
succession decisions. Like the crosswalk seam, a db-free build must be an
explicit choice, never the silent consequence of a missing env var.
"""

from datetime import date

import pytest
from ulid import ULID as _ULID

from clearinghouse_core.config import get_settings
from clearinghouse_domain_legislative.operator_events import OperatorEvent
from usa_wa_pipeline.operator_read import EventRow, operator_event_rows, operator_events


def test_events_are_empty_only_under_the_hermetic_marker(monkeypatch) -> None:
    monkeypatch.setenv("USA_WA_PIPELINE_HERMETIC", "1")
    assert operator_events() == []

    monkeypatch.delenv("USA_WA_PIPELINE_HERMETIC", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            operator_events()
    finally:
        get_settings.cache_clear()


def test_event_row_carries_the_overlay_attribute_surface() -> None:
    """`operator_overlay.from_rows` reads these five attributes by name."""
    row = EventRow(
        member_id="100",
        kind="departed",
        effective_date=None,
        seat_kind=None,
        seat_discriminator=None,
    )
    for attr in ("member_id", "kind", "effective_date", "seat_kind", "seat_discriminator"):
        assert hasattr(row, attr)


@pytest.mark.db
async def test_same_date_events_come_back_in_a_deterministic_order(db_session, usa_wa) -> None:
    """CR 61: `apply_operator_events` sorts STABLY on (phase, date), so input
    order decides same-date ties — and its per-span seating dedup makes which
    one wins outcome-affecting. Prod carries 7 such (member, date) pairs. A
    published dataset whose skip-if-unchanged gate hashes content must be
    reproducible from identical inputs, so the read cannot leave the tie to
    Postgres. The ULID key is curation order.

    Deliberately adversarial: the two rows are inserted in the OPPOSITE order
    to their ids. Reading them back in id order is then a property only the
    tiebreak can produce — with `order_by(effective_date)` alone Postgres hands
    back insertion order and this test goes red, which is the whole point.
    """
    when = date(2013, 6, 4)
    earlier_id, later_id = sorted((_ULID(), _ULID()))
    first = OperatorEvent(
        id=later_id,
        source_id="e1",
        member_id="17217",
        kind="vacated",
        reason="resignation",
        evidence_url="https://example.test/e1",
        entered_by="test",
        effective_date=when,
        seat_kind="chamber-senate",
        seat_discriminator="14",
    )
    db_session.add(first)
    await db_session.flush()
    second = OperatorEvent(
        id=earlier_id,
        source_id="e2",
        member_id="17217",
        kind="seated",
        reason="appointment",
        evidence_url="https://example.test/e2",
        entered_by="test",
        effective_date=when,
        seat_kind="chamber-senate",
        seat_discriminator="14",
    )
    db_session.add(second)
    await db_session.flush()

    # inserted vacated-then-seated, but seated holds the LOWER id
    rows = await operator_event_rows(db_session)
    assert [r.kind for r in rows] == ["seated", "vacated"]
