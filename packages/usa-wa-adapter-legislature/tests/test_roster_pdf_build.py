"""The #228 Phase B builder — archive → identities → Persons + spans, oracle-gated.

Runs against the real D2 fixture archived as the roster edition: the parse, identity
resolution, minting, projection and emission all exercise production code paths; only the
WSL seating index is empty (no sponsor archive here), so crossing folds refuse their join
and the build proceeds on the minted majority — which is itself the oracle's point: a
refusal is a tallied outcome, never an abort.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from clearinghouse_domain_legislative.identity import Assignment, Person
from usa_wa_adapter_legislature.roster_pdf.adapter import ROSTER_RESOURCE_PREFIX
from usa_wa_adapter_legislature.roster_pdf.build import (
    OracleViolation,
    build_pre1991,
    verify_pre1991,
)
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_MINTED,
    RosterIdentity,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source

CURRENT = "2025-26"


@pytest.fixture
async def archived_roster(db_session, usa_wa, roster_pdf_bytes):
    source = await get_or_create_roster_source(db_session, usa_wa)
    event = FetchEvent(
        source_id=source.id,
        resource_id=f"{ROSTER_RESOURCE_PREFIX}2025-06-05",
        url="https://x/roster.pdf",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=b"\x03" * 32,
        status=FetchStatus.ok,
    )
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        RawPayload(
            fetch_event_id=event.id,
            content_type="application/pdf",
            body=roster_pdf_bytes,
            size_bytes=len(roster_pdf_bytes),
        )
    )
    await db_session.flush()
    return source


async def test_build_mints_persons_and_emits_spans(db_session, usa_wa, archived_roster) -> None:
    summary = await build_pre1991(db_session, current_biennium=CURRENT)

    assert summary.persons_created > 0
    persons = (
        await db_session.execute(
            select(func.count()).select_from(Person).where(Person.source == ROSTER_SOURCE_SLUG)
        )
    ).scalar()
    assert persons == summary.persons_created
    assignments = (
        await db_session.execute(
            select(func.count())
            .select_from(Assignment)
            .where(Assignment.source == ROSTER_SOURCE_SLUG)
        )
    ).scalar()
    assert assignments == summary.assignments_emitted > 0
    # every emitted span is pre-1991 and closed — nothing the roster asserts is open
    open_count = (
        await db_session.execute(
            select(func.count())
            .select_from(Assignment)
            .where(Assignment.source == ROSTER_SOURCE_SLUG, Assignment.is_active.is_(True))
        )
    ).scalar()
    assert open_count == 0


async def test_build_is_idempotent(db_session, usa_wa, archived_roster) -> None:
    first = await build_pre1991(db_session, current_biennium=CURRENT)
    second = await build_pre1991(db_session, current_biennium=CURRENT)
    assert second.persons_created == 0
    assert second.persons_existing == first.persons_created
    assert second.assignments_emitted == first.assignments_emitted  # upserts, not duplicates


def _rec(name: str, year: int, **kw) -> RosterRecord:
    defaults = dict(district=1, chamber="senate", order=1, party_token="D", annotation=None)
    defaults.update(kw)
    return RosterRecord(year=year, name=name, page_number=1, **defaults)


def test_oracle_rejects_person_side_senate_simultaneity() -> None:
    """Oracle item 3, the person side: one member covering two Senate seats in one
    biennium is corrupt data, and nothing downstream checks it — abort, with subjects."""
    identity = RosterIdentity(
        disposition=IDENTITY_MINTED,
        fold="xdouble",
        key="xdouble:1901",
        wsl_member_id=None,
        records=(
            _rec("X. Double", 1901, district=1),
            _rec("X. Double", 1901, district=2),
        ),
    )
    with pytest.raises(OracleViolation, match="xdouble"):
        verify_pre1991([identity], [r for r in identity.records])
