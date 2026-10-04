"""The curated operator read seams: events (#309) and succession links (#447).

Operator events are the span transform's one non-raw-store input — human
succession decisions. Like the crosswalk seam, a db-free build must be an
explicit choice, never the silent consequence of a missing env var.
"""

from datetime import date

import pytest
from ulid import ULID as _ULID

from clearinghouse_core.config import get_settings
from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from clearinghouse_domain_legislative.operator_events import OperatorEvent
from usa_wa_pipeline.operator_read import (
    EventRow,
    SuccessionRow,
    operator_event_rows,
    operator_events,
    succession_link_rows,
    succession_links,
)


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


def test_succession_links_are_empty_only_under_the_hermetic_marker(monkeypatch) -> None:
    """#447: the lineage read is a curated input like the operator events — a
    db-free build says so explicitly, never by a missing env var."""
    monkeypatch.setenv("USA_WA_PIPELINE_HERMETIC", "1")
    assert succession_links() == []

    monkeypatch.delenv("USA_WA_PIPELINE_HERMETIC", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            succession_links()
    finally:
        get_settings.cache_clear()


def _link(
    source_id: str, subject: str, linked: str, slug: str, **extra
) -> CommitteeSuccessionEvent:
    return CommitteeSuccessionEvent(
        source_id=source_id,
        subject_source_id=subject,
        linked_source_id=linked,
        slug=slug,
        evidence_url=f"https://example.test/{source_id}",
        **extra,
    )


@pytest.mark.db
async def test_succession_rows_skip_superseded_links_and_carry_the_published_fields(
    db_session,
) -> None:
    """Only the current attestation publishes: a supersede retracts the old row
    from the next version (#447 scope 1)."""
    corrected = _link(
        "succeeded_by:35341:36500:2026",
        "35341",
        "36500",
        "succeeded_by",
        effective_year=2026,
        notes="SCR 8406",
    )
    db_session.add(corrected)
    await db_session.flush()
    stale = _link(
        "succeeded_by:35341:36500:2025",
        "35341",
        "36500",
        "succeeded_by",
        effective_year=2025,
        superseded_by_id=corrected.id,
    )
    db_session.add(stale)
    await db_session.flush()

    rows = await succession_link_rows(db_session)
    assert rows == [
        SuccessionRow(
            subject_source_id="35341",
            linked_source_id="36500",
            slug="succeeded_by",
            effective_year=2026,
            evidence_url="https://example.test/succeeded_by:35341:36500:2026",
            notes="SCR 8406",
        )
    ]


@pytest.mark.db
async def test_succession_rows_come_back_in_a_deterministic_order(db_session) -> None:
    """A content-hashed dataset must reproduce from identical inputs; inserted
    out of order, read back by (subject, slug, linked)."""
    for source_id, subject, linked, slug in (
        ("succeeded_by:9:3", "9", "3", "succeeded_by"),
        ("split_from:2:1", "2", "1", "split_from"),
        ("merged_with:2:5", "2", "5", "merged_with"),
    ):
        db_session.add(_link(source_id, subject, linked, slug))
    await db_session.flush()

    rows = await succession_link_rows(db_session)
    assert [(r.subject_source_id, r.slug, r.linked_source_id) for r in rows] == [
        ("2", "merged_with", "5"),
        ("2", "split_from", "1"),
        ("9", "succeeded_by", "3"),
    ]
