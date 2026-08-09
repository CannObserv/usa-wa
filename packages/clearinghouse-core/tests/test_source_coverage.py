"""Source coverage as data (#180) — the claim declaration, and the table it seeds."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from clearinghouse_core.provenance import Source
from clearinghouse_core.source_coverage import (
    STATUS_CHECK_NAME,
    STATUSES,
    CoverageClaim,
    CoverageStatus,
    SourceCoverage,
    claim_for,
    known_gaps,
    seed_source_coverage,
    status_check_sql,
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


def test_a_claim_rejects_a_bound_that_does_not_lead_with_a_year():
    """``floor_year`` slices the first four characters, so a malformed bound would not fail
    where it was declared — it would raise ``int()`` at whichever CLI imported it (CR #196
    finding 28). Validate at construction, next to the other two checks."""
    with pytest.raises(ValueError, match="four-digit year"):
        _claim(range_start="91-92", range_end=None)
    with pytest.raises(ValueError, match="four-digit year"):
        _claim(range_start="2008", range_end="20xx")


def test_claim_for_selects_by_dimension_and_status():
    """A source can hold several claims on one dimension — the served span and the ``absent``
    span it does not serve. Selecting by status is what keeps a known gap addressable."""
    served = _claim()
    gap = _claim(range_start="2020", range_end=None, status=CoverageStatus.absent, notes="retired")
    claims = (served, gap)

    assert claim_for(claims, "election_year") is served
    assert claim_for(claims, "election_year", status=CoverageStatus.absent) is gap
    assert known_gaps(claims) == (gap,)

    with pytest.raises(LookupError, match="no_such_dimension"):
        claim_for(claims, "no_such_dimension")


def test_claim_for_defaults_to_any_served_status_so_a_promotion_is_not_breaking():
    """Re-auditing an ``assumed`` claim into a ``verified`` one is the *intended* workflow, and
    it must not break the CLI that derives its floor from the claim (CR #196 finding 24).

    The floor derivations are module-level, so pinning ``status=assumed`` at the call site made
    the promotion raise ``LookupError`` at **import** — ``harvest_committee_members`` and
    ``usa_wa_adapter_pdc.harvest`` both died on it. Default to "the one claim this source
    actually serves on this dimension", whatever established it.
    """
    assumed = _claim(status=CoverageStatus.assumed, notes="never probed")
    assert claim_for((assumed,), "election_year") is assumed

    promoted = _claim(status=CoverageStatus.verified, notes="probed live")
    assert claim_for((promoted,), "election_year") is promoted


def test_claim_for_never_defaults_onto_a_gap():
    """``absent`` is a claim about what is *not* served, so it can never answer "what is the
    floor" — it has to be asked for by name."""
    gap = _claim(status=CoverageStatus.absent, notes="retired")
    with pytest.raises(LookupError, match="served coverage claim"):
        claim_for((gap,), "election_year")


def test_the_status_check_sql_is_derived_from_the_vocabulary():
    """Adding a status must move the constraint with it — CR #191 finding 5, one table over."""
    sql = status_check_sql()
    for status in STATUSES:
        assert f"'{status}'" in sql


def test_model_check_constraint_matches_the_migrations_copy():
    """``alembic/versions/`` cannot import this module without coupling a historical migration
    to a live one, so the migration keeps its own literal. This is the seam that keeps the two
    honest (the same pin ``test_runs.py`` carries for ``job_runs.outcome``)."""
    migration = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "02bb603b7702_180_source_coverage.py"
    ).read_text()
    for status in STATUSES:
        assert f"'{status}'" in migration, f"{status!r} missing from the migration's CHECK"
    assert STATUS_CHECK_NAME in migration


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


async def test_seed_retires_a_row_whose_range_start_moved(db_session, usa_wa):
    """A re-audit that moves the **floor** is the commonest re-audit outcome, and it changes the
    row's own key (CR #196 finding 27).

    Keyed on ``(dimension, range_start)``, the moved claim inserted a second row and left the
    first — so the source claimed both a 2008 floor and a 2006 one, which is exactly the
    "answers twice and disagrees with itself" this function exists to prevent. The seed is a
    full reconcile of the declared set, not an upsert over it.
    """
    source = await _source(db_session, usa_wa)
    await seed_source_coverage(db_session, source, (_claim(),))

    moved = _claim(range_start="2006", notes="re-probed: the archive reaches back further")
    assert await seed_source_coverage(db_session, source, (moved,)) == 2  # 1 insert + 1 retire

    rows = (await db_session.execute(select(SourceCoverage))).scalars().all()
    assert [(r.range_start, r.range_end) for r in rows] == [("2006", "2018")]


async def test_seed_leaves_a_sibling_claim_on_the_same_dimension_alone(db_session, usa_wa):
    """The reconcile must not mistake the ``absent`` sibling for a stale row.

    Filings deliberately holds two ``election_year`` claims — ``verified`` 2008-2018 and
    ``absent`` 2020-onward — so retiring "everything else on this dimension" would delete the
    gap that is the whole point of the table.
    """
    source = await _source(db_session, usa_wa)
    claims = (
        _claim(),
        _claim(range_start="2020", range_end=None, status=CoverageStatus.absent, notes="retired"),
    )
    await seed_source_coverage(db_session, source, claims)

    assert await seed_source_coverage(db_session, source, claims) == 0

    rows = (await db_session.execute(select(SourceCoverage))).scalars().all()
    assert sorted(r.range_start for r in rows) == ["2008", "2020"]


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
