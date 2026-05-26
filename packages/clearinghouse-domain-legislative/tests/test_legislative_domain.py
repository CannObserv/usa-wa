"""Smoke imports + one instantiation per cluster (Bill / Statute / Filer).

The legislative-domain entities don't have their own alembic migration yet
(deferred to P1a, after the multi-state IA delta from P0 step 10). For now
we lean on ``Base.metadata.create_all`` in the test fixture to materialize
the tables and confirm the SQLAlchemy declarations are correct.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

# Importing the package side-effects in the three sub-modules so the tables
# register on Base.metadata before the test_engine fixture creates them.
import clearinghouse_domain_legislative  # noqa: F401
from clearinghouse_domain_legislative.bills import Bill
from clearinghouse_domain_legislative.pdc import Filer
from clearinghouse_domain_legislative.statutes import (
    StatuteChapter,
    StatuteCode,
    StatuteSection,
    StatuteTitle,
)


def test_package_imports_three_clusters():
    """The package re-exports its three model clusters."""
    assert hasattr(clearinghouse_domain_legislative, "bills")
    assert hasattr(clearinghouse_domain_legislative, "statutes")
    assert hasattr(clearinghouse_domain_legislative, "pdc")


async def test_bill_round_trip(db_session):
    """A Bill row persists and reads back with all the MVP columns populated."""
    bill = Bill(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="HB-1234-2025-26",
        biennium="2025-26",
        chamber="house",
        number=1234,
        bill_type="HB",
        title="An act relating to widget regulation",
        short_description="Widget reg",
        current_status="In Rules Committee",
        current_step="house_rules_review",
        introduced_at=datetime(2025, 1, 15, tzinfo=UTC),
    )
    db_session.add(bill)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Bill).where(Bill.source_id == "HB-1234-2025-26"))
    ).scalar_one()
    assert fetched.title == "An act relating to widget regulation"
    assert fetched.number == 1234
    assert fetched.jurisdiction_id == "usa-wa"


async def test_statute_chain_round_trip(db_session):
    """A StatuteCode → Title → Chapter → Section chain persists with the natural keys intact."""
    code = StatuteCode(
        jurisdiction_id="usa-wa",
        code="RCW",
        name="Revised Code of Washington",
    )
    db_session.add(code)
    await db_session.flush()

    title = StatuteTitle(
        jurisdiction_id="usa-wa", statute_code_id=code.id, number="46", heading="Motor Vehicles"
    )
    db_session.add(title)
    await db_session.flush()

    chapter = StatuteChapter(
        jurisdiction_id="usa-wa",
        statute_title_id=title.id,
        number="46.16",
        heading="Vehicle Registration",
    )
    db_session.add(chapter)
    await db_session.flush()

    section = StatuteSection(
        jurisdiction_id="usa-wa",
        statute_chapter_id=chapter.id,
        number="46.16.005",
        heading="Definitions",
        text="As used in this chapter ...",
    )
    db_session.add(section)
    await db_session.flush()

    found = (
        await db_session.execute(select(StatuteSection).where(StatuteSection.number == "46.16.005"))
    ).scalar_one()
    assert found.heading == "Definitions"
    assert found.statute_chapter_id == chapter.id


async def test_filer_round_trip(db_session):
    """A PDC Filer row persists with powermap link columns nullable until P2."""
    filer = Filer(
        jurisdiction_id="usa-wa",
        source="usa_wa_pdc",
        source_id="L-12345",
        name="Acme Government Affairs LLC",
        filer_type="lobbyist",
    )
    db_session.add(filer)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Filer).where(Filer.source_id == "L-12345"))
    ).scalar_one()
    assert fetched.name == "Acme Government Affairs LLC"
    assert fetched.filer_type == "lobbyist"
    assert fetched.powermap_org_id is None
    assert fetched.powermap_person_id is None


async def test_contribution_uses_decimal_money(db_session):
    """Contribution.amount is Numeric, not float — money math stays exact."""
    from clearinghouse_domain_legislative.pdc import Contribution

    recipient = Filer(
        jurisdiction_id="usa-wa",
        source="usa_wa_pdc",
        source_id="C-001",
        name="Friends of Jane Doe",
        filer_type="candidate_committee",
    )
    db_session.add(recipient)
    await db_session.flush()

    c = Contribution(
        jurisdiction_id="usa-wa",
        source="usa_wa_pdc",
        source_id="CON-001",
        recipient_filer_id=recipient.id,
        contributor_name_raw="John Smith",
        amount=Decimal("1234.56"),
        contributed_at=datetime(2026, 3, 15, tzinfo=UTC),
    )
    db_session.add(c)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Contribution).where(Contribution.source_id == "CON-001"))
    ).scalar_one()
    assert fetched.amount == Decimal("1234.56")
