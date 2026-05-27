"""Smoke imports + one instantiation per cluster (Person / Organization / Role /
Assignment / Bill / Amendment / VoteEvent / Statute / Filer-via-Org / Contribution).

The legislative-domain entities don't have their own alembic migration yet
(deferred to P0.5 step 5). Tests use ``Base.metadata.create_all`` to materialize
the tables in the test DB.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

import clearinghouse_domain_legislative  # noqa: F401  (side-effect registration)
from clearinghouse_domain_legislative.bills import (
    Amendment,
    Bill,
    BillAction,
    BillSponsorship,
    BillTitle,
)
from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    PersonIdentifier,
    Role,
)
from clearinghouse_domain_legislative.pdc import Contribution, LobbyingActivity
from clearinghouse_domain_legislative.sessions import LegislativeSession
from clearinghouse_domain_legislative.statutes import (
    StatuteChapter,
    StatuteCode,
    StatuteSection,
    StatuteTitle,
)
from clearinghouse_domain_legislative.votes import PersonVote, VoteCount, VoteEvent


def test_package_imports_six_clusters():
    """All six legislative-domain sub-modules are re-exported."""
    for name in ("identity", "sessions", "bills", "votes", "statutes", "pdc"):
        assert hasattr(clearinghouse_domain_legislative, name)


async def test_identity_round_trip(db_session):
    """Person + Organization + Role + Assignment chain persists with all natural keys intact."""
    person = Person(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="26142",
        name_full="Jane Doe",
        name_first="Jane",
        name_last="Doe",
    )
    db_session.add(person)
    await db_session.flush()

    senate = Organization(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="senate",
        name="Washington State Senate",
        short_name="Senate",
        org_type="chamber",
    )
    db_session.add(senate)
    await db_session.flush()

    senator_role = Role(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="role:senate:senator:21",
        organization_id=senate.id,
        name="Senator",
        role_type="elected_member",
        district="21",
    )
    db_session.add(senator_role)
    await db_session.flush()

    assignment = Assignment(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="assignment:26142:senator:21:2023-01-09",
        person_id=person.id,
        role_id=senator_role.id,
        valid_from=date(2023, 1, 9),
        is_active=True,
    )
    db_session.add(assignment)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Assignment).where(Assignment.person_id == person.id))
    ).scalar_one()
    assert fetched.role_id == senator_role.id
    assert fetched.valid_to is None
    assert fetched.is_active is True


async def test_person_identifier_round_trip(db_session):
    """An external-ID mapping persists with the (jurisdiction, scheme, value) unique constraint."""
    person = Person(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="26143",
        name_full="John Smith",
    )
    db_session.add(person)
    await db_session.flush()

    ident = PersonIdentifier(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="ident:26143:wsl_member_id",
        person_id=person.id,
        scheme="wsl_member_id",
        value="26143",
    )
    db_session.add(ident)
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(PersonIdentifier).where(PersonIdentifier.scheme == "wsl_member_id")
        )
    ).scalar_one()
    assert fetched.value == "26143"
    assert fetched.person_id == person.id


async def test_bill_with_session_round_trip(db_session):
    """A Bill with all the v1 columns (chambers, status_class, enacted_as) persists."""
    session = LegislativeSession(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="2025",
        slug="usa-wa-2025",
        name="2025 Regular Session",
        classification="regular",
        start_date=date(2025, 1, 13),
        biennium_label="2025-26",
        is_active=True,
    )
    db_session.add(session)
    await db_session.flush()

    bill = Bill(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="HB-1234-2025-26",
        legislative_session_id=session.id,
        originating_chamber="house",
        current_chamber="senate",
        number=1234,
        bill_type="HB",
        title="An act relating to widgets",
        short_description="Comprehensive widget regulation reform",
        current_status="In Senate Rules Committee",
        current_status_class="passed_first_chamber",
        current_status_at=datetime(2025, 3, 1, tzinfo=UTC),
        introduced_at=datetime(2025, 1, 15, tzinfo=UTC),
    )
    db_session.add(bill)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(Bill).where(Bill.source_id == "HB-1234-2025-26"))
    ).scalar_one()
    assert fetched.originating_chamber == "house"
    assert fetched.current_chamber == "senate"
    assert fetched.current_status_class == "passed_first_chamber"
    assert fetched.legislative_session_id == session.id


async def test_polymorphic_sponsorship_person(db_session):
    """A person-sponsored Bill works without organization_id."""
    person = Person(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="26144",
        name_full="Sponsor Senator",
    )
    session = LegislativeSession(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="2025",
        slug="usa-wa-2025",
        name="2025 Regular Session",
        classification="regular",
    )
    db_session.add_all([person, session])
    await db_session.flush()

    bill = Bill(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="HB-9999-2025-26",
        legislative_session_id=session.id,
        originating_chamber="house",
        number=9999,
        title="Test bill",
    )
    db_session.add(bill)
    await db_session.flush()

    sponsorship = BillSponsorship(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="sp:HB-9999-2025-26:primary:26144",
        bill_id=bill.id,
        person_id=person.id,
        role="primary",
        sponsor_order=1,
    )
    db_session.add(sponsorship)
    await db_session.flush()

    fetched = (
        await db_session.execute(select(BillSponsorship).where(BillSponsorship.bill_id == bill.id))
    ).scalar_one()
    assert fetched.person_id == person.id
    assert fetched.organization_id is None
    assert fetched.role == "primary"


async def test_amendment_and_vote_event_round_trip(db_session):
    """Amendment + VoteEvent + VoteCount + PersonVote chain — polymorphic vote subject."""
    session = LegislativeSession(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="2025",
        slug="usa-wa-2025",
        name="2025 Regular Session",
        classification="regular",
    )
    senate = Organization(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="senate",
        name="WA Senate",
        org_type="chamber",
    )
    person = Person(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="26145",
        name_full="Vote Caster",
    )
    db_session.add_all([session, senate, person])
    await db_session.flush()

    bill = Bill(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="HB-7777-2025-26",
        legislative_session_id=session.id,
        originating_chamber="house",
        number=7777,
        title="Bill under amendment",
    )
    db_session.add(bill)
    await db_session.flush()

    amendment = Amendment(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="amd:HB-7777-2025-26:1",
        bill_id=bill.id,
        label="Amendment 1",
        status="adopted",
        offered_at=datetime(2025, 2, 1, tzinfo=UTC),
        adopted_at=datetime(2025, 2, 5, tzinfo=UTC),
    )
    db_session.add(amendment)
    await db_session.flush()

    vote = VoteEvent(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="rc:senate:amd:HB-7777-2025-26:1",
        subject_type="amendment",
        subject_id=amendment.id,
        amendment_id=amendment.id,
        bill_id=bill.id,
        context_type="floor",
        context_organization_id=senate.id,
        chamber="senate",
        category="procedural",
        event_at=datetime(2025, 2, 5, 14, 0, tzinfo=UTC),
        outcome="passed",
    )
    db_session.add(vote)
    await db_session.flush()

    count = VoteCount(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="vc:rc:senate:amd:1:yea",
        vote_event_id=vote.id,
        count_type="yea",
        value=29,
    )
    pv = PersonVote(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="pv:rc:senate:amd:1:26145",
        vote_event_id=vote.id,
        person_id=person.id,
        vote="yea",
    )
    db_session.add_all([count, pv])
    await db_session.flush()

    fetched_pv = (
        await db_session.execute(select(PersonVote).where(PersonVote.person_id == person.id))
    ).scalar_one()
    assert fetched_pv.vote_event_id == vote.id
    assert fetched_pv.vote == "yea"


async def test_statute_chain_round_trip(db_session):
    """The statute cluster is unchanged from P0 — its natural keys are (jurisdiction, code) etc.,
    not the universal (jurisdiction, source, source_id). No source/source_id columns yet."""
    code = StatuteCode(
        jurisdiction_id="usa-wa",
        code="RCW",
        name="Revised Code of Washington",
    )
    db_session.add(code)
    await db_session.flush()
    title = StatuteTitle(
        jurisdiction_id="usa-wa",
        statute_code_id=code.id,
        number="46",
        heading="Motor Vehicles",
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
    fetched = (
        await db_session.execute(select(StatuteSection).where(StatuteSection.number == "46.16.005"))
    ).scalar_one()
    assert fetched.heading == "Definitions"


async def test_lobbying_and_contribution_round_trip(db_session):
    """PDC entities reshape around Person+Organization; CHECK constraints hold."""
    lobbyist = Organization(
        jurisdiction_id="usa-wa",
        source="usa_wa_pdc",
        source_id="L-12345",
        name="Acme Government Affairs LLC",
        org_type="lobbying_firm",
    )
    committee = Organization(
        jurisdiction_id="usa-wa",
        source="usa_wa_pdc",
        source_id="C-001",
        name="Friends of Jane Doe",
        org_type="candidate_committee",
    )
    db_session.add_all([lobbyist, committee])
    await db_session.flush()

    activity = LobbyingActivity(
        jurisdiction_id="usa-wa",
        source="usa_wa_pdc",
        source_id="LA-2025-Q1-12345",
        organization_id=lobbyist.id,
        period_start=date(2025, 1, 1),
        period_end=date(2025, 3, 31),
        compensation=Decimal("50000.00"),
        expenses=Decimal("1234.56"),
    )
    db_session.add(activity)
    await db_session.flush()
    assert activity.compensation == Decimal("50000.00")

    contribution = Contribution(
        jurisdiction_id="usa-wa",
        source="usa_wa_pdc",
        source_id="CON-001",
        recipient_organization_id=committee.id,
        contributor_name_raw="Anonymous Donor",
        amount=Decimal("100.00"),
        contributed_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    db_session.add(contribution)
    await db_session.flush()
    fetched = (
        await db_session.execute(select(Contribution).where(Contribution.source_id == "CON-001"))
    ).scalar_one()
    assert fetched.recipient_organization_id == committee.id
    assert fetched.amount == Decimal("100.00")


async def test_bill_action_polymorphic_classifications(db_session):
    """BillAction grows multi-class via BillActionClassification 1:N child table."""
    from clearinghouse_domain_legislative.bills import BillActionClassification

    session = LegislativeSession(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="2025",
        slug="usa-wa-2025",
        name="2025 Regular Session",
        classification="regular",
    )
    db_session.add(session)
    await db_session.flush()
    bill = Bill(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="HB-3333-2025-26",
        legislative_session_id=session.id,
        originating_chamber="house",
        number=3333,
        title="Multi-class action bill",
    )
    db_session.add(bill)
    await db_session.flush()

    action = BillAction(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="act:HB-3333:reading3-and-passage",
        bill_id=bill.id,
        action_at=datetime(2025, 3, 10, tzinfo=UTC),
        chamber="house",
        action_type="Third reading, final passage",
        primary_classification="passage",
        description="Passed the House on third reading.",
        display_order=1,
        is_major=True,
    )
    db_session.add(action)
    await db_session.flush()

    db_session.add_all(
        [
            BillActionClassification(
                jurisdiction_id="usa-wa",
                source="usa_wa_legislature",
                source_id=f"bac:{action.source_id}:reading-3",
                bill_action_id=action.id,
                classification="reading-3",
            ),
            BillActionClassification(
                jurisdiction_id="usa-wa",
                source="usa_wa_legislature",
                source_id=f"bac:{action.source_id}:passage",
                bill_action_id=action.id,
                classification="passage",
            ),
        ]
    )
    await db_session.flush()

    classes = (
        (
            await db_session.execute(
                select(BillActionClassification).where(
                    BillActionClassification.bill_action_id == action.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {c.classification for c in classes} == {"reading-3", "passage"}


async def test_bill_titles_1_to_n_with_amendment_provenance(db_session):
    """Bills carry multiple titles via BillTitle; amendment_id tracks WA's
    amendment-driven title changes."""
    session = LegislativeSession(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="2025",
        slug="usa-wa-2025",
        name="2025 Regular Session",
        classification="regular",
    )
    db_session.add(session)
    await db_session.flush()
    bill = Bill(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="HB-4242-2025-26",
        legislative_session_id=session.id,
        originating_chamber="house",
        number=4242,
        title="An act relating to widget regulation",  # denormalized
    )
    db_session.add(bill)
    await db_session.flush()

    amendment = Amendment(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="amd:HB-4242:21",
        bill_id=bill.id,
        label="Striking Amendment 21",
        status="adopted",
    )
    db_session.add(amendment)
    await db_session.flush()

    canonical_at_intro = BillTitle(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="title:HB-4242:canonical:intro",
        bill_id=bill.id,
        title_text="An act relating to widget manufacturing",
        title_type="canonical",
        as_of_action="introduced",
        is_current=False,
        replaced_at=datetime(2025, 3, 15, tzinfo=UTC),
    )
    canonical_current = BillTitle(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="title:HB-4242:canonical:current",
        bill_id=bill.id,
        title_text="An act relating to widget regulation",
        title_type="canonical",
        as_of_action="committee_substitute",
        amendment_id=amendment.id,
        effective_at=datetime(2025, 3, 15, tzinfo=UTC),
        is_current=True,
    )
    short_title = BillTitle(
        jurisdiction_id="usa-wa",
        source="usa_wa_legislature",
        source_id="title:HB-4242:short",
        bill_id=bill.id,
        title_text="Widget Reform Act",
        title_type="short",
        is_current=True,
    )
    db_session.add_all([canonical_at_intro, canonical_current, short_title])
    await db_session.flush()

    titles = (
        (await db_session.execute(select(BillTitle).where(BillTitle.bill_id == bill.id)))
        .scalars()
        .all()
    )
    assert len(titles) == 3
    # The current canonical title matches Bill.title (denormalization invariant)
    current_canonical = next(t for t in titles if t.title_type == "canonical" and t.is_current)
    assert current_canonical.title_text == bill.title
    # The current canonical title was set by an amendment
    assert current_canonical.amendment_id == amendment.id
    # The pre-amendment title is preserved with replaced_at
    pre_amend = next(t for t in titles if t.title_type == "canonical" and not t.is_current)
    assert pre_amend.replaced_at is not None
    assert pre_amend.amendment_id is None  # Was the introduced title, not amendment-driven
