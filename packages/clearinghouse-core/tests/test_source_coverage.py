"""Source coverage as data (#180) — the claim declaration, and the table it seeds."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from clearinghouse_core.provenance import Source
from clearinghouse_core.source_coverage import (
    CoverageClaim,
    CoverageStatus,
    SourceCoverage,
    claim_for,
    known_gaps,
    seed_source_coverage,
)

_AUDITED = date(2026, 8, 6)


def _claim(**overrides) -> CoverageClaim:
    kwargs = {
        "source_slug": "demo",
        "dimension": "election_year",
        "range_start": "2008",
        "range_end": "2018",
        "status": CoverageStatus.verified,
        "audited_at": _AUDITED,
        "notes": "probed live",
    }
    return CoverageClaim(**(kwargs | overrides))


async def _source(db_session, usa_wa, slug="demo") -> Source:
    row = Source(jurisdiction_id=usa_wa.id, name=slug, slug=slug, kind="rest")
    db_session.add(row)
    await db_session.flush()
    return row


# --- the declaration (pure, no database) ------------------------------------


def test_floor_and_ceiling_read_the_leading_year_of_either_range_form():
    """A range bound is a string because the unit varies by dimension — a bare election year
    (``2008``) or a WA biennium label (``1991-92``). Both carry their year in the first four
    characters, which is what a year-keyed sweep needs."""
    years = _claim()
    assert years.floor_year == 2008
    assert years.ceiling_year == 2018

    bienniums = _claim(dimension="sponsor_roster", range_start="1991-92", range_end=None)
    assert bienniums.floor_year == 1991
    assert bienniums.ceiling_year is None  # open-ended — the feed still serves


def test_a_claim_rejects_an_inverted_range():
    with pytest.raises(ValueError, match="range_end"):
        _claim(range_start="2018", range_end="2008")


def test_a_claim_requires_a_reason():
    """``notes`` is the audit's own record of *how* the bound was established. A coverage row
    with no justification is the prose-in-a-comment problem again, one table over."""
    with pytest.raises(ValueError, match="notes"):
        _claim(notes="")


def test_claim_for_selects_by_dimension_and_status():
    """A source can hold several claims on one dimension — the verified span it serves and the
    ``absent`` span it does not. Selecting by status is what keeps a known gap addressable."""
    served = _claim()
    gap = _claim(range_start="2020", range_end=None, status=CoverageStatus.absent, notes="retired")
    claims = (served, gap)

    assert claim_for(claims, "election_year") is served
    assert claim_for(claims, "election_year", status=CoverageStatus.absent) is gap
    assert known_gaps(claims) == (gap,)

    with pytest.raises(LookupError, match="no_such_dimension"):
        claim_for(claims, "no_such_dimension")


def test_claim_for_refuses_an_ambiguous_match():
    """Two verified spans on one dimension means a builder asking for "the floor" has no single
    answer — fail rather than silently pick one."""
    claims = (_claim(), _claim(range_start="2020", range_end="2024"))
    with pytest.raises(LookupError, match="ambiguous"):
        claim_for(claims, "election_year")


# --- the table --------------------------------------------------------------


async def test_seed_writes_each_claim_including_the_absent_one(db_session, usa_wa):
    """``absent`` is the load-bearing value: it lets a known gap be a **fact** the system can
    answer with, rather than the silence a missing row is indistinguishable from."""
    source = await _source(db_session, usa_wa)
    claims = (
        _claim(),
        _claim(range_start="2020", range_end=None, status=CoverageStatus.absent, notes="retired"),
    )

    written = await seed_source_coverage(db_session, source, claims)
    assert written == 2

    rows = (
        (
            await db_session.execute(
                select(SourceCoverage)
                .where(SourceCoverage.source_id == source.id)
                .order_by(SourceCoverage.range_start)
            )
        )
        .scalars()
        .all()
    )
    assert [(r.range_start, r.range_end, r.status) for r in rows] == [
        ("2008", "2018", CoverageStatus.verified.value),
        ("2020", None, CoverageStatus.absent.value),
    ]
    assert rows[0].dimension == "election_year"
    assert rows[0].audited_at.date() == _AUDITED
    assert rows[0].notes == "probed live"


async def test_seed_is_idempotent(db_session, usa_wa):
    source = await _source(db_session, usa_wa)
    claims = (_claim(),)
    assert await seed_source_coverage(db_session, source, claims) == 1
    assert await seed_source_coverage(db_session, source, claims) == 0
    assert len((await db_session.execute(select(SourceCoverage))).scalars().all()) == 1


async def test_seed_updates_a_re_audited_claim_in_place(db_session, usa_wa):
    """A re-audit that finds the feed's range moved must land on the existing row, not mint a
    second one — otherwise "what do we cover?" answers twice and disagrees with itself."""
    source = await _source(db_session, usa_wa)
    await seed_source_coverage(db_session, source, (_claim(),))

    re_audited = _claim(range_end="2020", audited_at=date(2026, 9, 1), notes="feed came back")
    assert await seed_source_coverage(db_session, source, (re_audited,)) == 1

    rows = (await db_session.execute(select(SourceCoverage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].range_end == "2020"
    assert rows[0].audited_at.date() == date(2026, 9, 1)
    assert rows[0].notes == "feed came back"


async def test_status_vocabulary_is_check_constrained(db_session, usa_wa):
    """The three statuses mean different things to a builder — a CHECK stops a fourth from
    being invented per-adapter, the same discipline job_runs.outcome carries (#178)."""
    source = await _source(db_session, usa_wa)
    db_session.add(
        SourceCoverage(
            source_id=source.id,
            dimension="election_year",
            range_start="2008",
            status="probably",
            audited_at=datetime.now(UTC),
            notes="nope",
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()


async def test_one_row_per_source_dimension_and_start(db_session, usa_wa):
    source = await _source(db_session, usa_wa)
    for _ in range(2):
        db_session.add(
            SourceCoverage(
                source_id=source.id,
                dimension="election_year",
                range_start="2008",
                range_end="2018",
                status=CoverageStatus.verified.value,
                audited_at=datetime.now(UTC),
                notes="dup",
            )
        )
    with pytest.raises(IntegrityError):
        await db_session.flush()
