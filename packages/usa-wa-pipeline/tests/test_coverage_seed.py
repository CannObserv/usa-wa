"""The nightly reconciles every source's declared coverage (#412 PR E, CR 1).

``source_coverage`` serves ``/sources/{slug}/coverage`` (#180) and survived the canonical
tier. Its only writer was each adapter's ``provisioning``, called by the daily refreshes
PR E disabled, so without this job a re-audited claim merged to main would never reach
the API.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from clearinghouse_core.source_coverage import SourceCoverage
from clearinghouse_core.sources import Source
from usa_wa_adapter_legislature.coverage import WSL_COVERAGE, WSL_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_COVERAGE, ROSTER_SOURCE_SLUG
from usa_wa_adapter_pdc.coverage import PDC_COVERAGE, PDC_SOURCE_SLUG
from usa_wa_adapter_sos.coverage import (
    SOS_FILINGS_COVERAGE,
    SOS_FILINGS_SOURCE_SLUG,
    SOS_RESULTS_COVERAGE,
    SOS_RESULTS_SOURCE_SLUG,
)
from usa_wa_pipeline.coverage_seed import JOB_SLUG, reconcile_coverage

DECLARED = {
    WSL_SOURCE_SLUG: WSL_COVERAGE,
    ROSTER_SOURCE_SLUG: ROSTER_COVERAGE,
    PDC_SOURCE_SLUG: PDC_COVERAGE,
    SOS_FILINGS_SOURCE_SLUG: SOS_FILINGS_COVERAGE,
    SOS_RESULTS_SOURCE_SLUG: SOS_RESULTS_COVERAGE,
}


async def _served(session) -> dict[str, set[tuple]]:
    rows = (
        await session.execute(
            select(
                Source.slug,
                SourceCoverage.dimension,
                SourceCoverage.range_start,
                SourceCoverage.range_end,
                SourceCoverage.status,
            ).join(SourceCoverage, SourceCoverage.source_id == Source.id)
        )
    ).all()
    served: dict[str, set[tuple]] = {}
    for slug, *claim in rows:
        served.setdefault(slug, set()).add(tuple(claim))
    return served


def _declared(claims) -> set[tuple]:
    return {(c.dimension, c.range_start, c.range_end, c.status.value) for c in claims}


def test_slug_is_stable() -> None:
    assert JOB_SLUG == "coverage-seed"


@pytest.mark.db
async def test_every_declared_claim_is_served(db_session, usa_wa) -> None:
    counters = await reconcile_coverage(db_session)

    assert counters["sources"] == len(DECLARED)
    served = await _served(db_session)
    for slug, claims in DECLARED.items():
        assert served.get(slug, set()) == _declared(claims), slug


@pytest.mark.db
async def test_a_stale_served_claim_is_reconciled(db_session, usa_wa) -> None:
    """The case the job exists for: the served row disagrees with the code."""
    await reconcile_coverage(db_session)
    row = (
        (
            await db_session.execute(
                select(SourceCoverage)
                .join(Source, Source.id == SourceCoverage.source_id)
                .where(Source.slug == WSL_SOURCE_SLUG)
            )
        )
        .scalars()
        .first()
    )
    row.range_end = "1999-00"
    await db_session.flush()

    await reconcile_coverage(db_session)

    assert (await _served(db_session))[WSL_SOURCE_SLUG] == _declared(WSL_COVERAGE)
