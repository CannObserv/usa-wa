"""Phase B PDC span builder (#79) — archive → era-matched House Position spans + identifiers.

Reads every archived ``house-winners:<Y>`` / ``senate-winners:<Y>`` cohort **offline** (via
:class:`~usa_wa_adapter_pdc.pdc_cohort.PdcWinnerCohortProvider`), pairs each with the roster of
the biennium it **seated** (``[Y+1, Y+2]``, read archive-first from the WSL sponsor archive),
projects observations + ``person_wa_pdc`` links, merges the House observations across years into
:class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s, and emits one ``usa_wa_pdc``
Assignment per contiguous House Position tenure plus the identifier links.

**The #75 fix.** Each cohort matches the roster of its *own* era, not the current one — so a
2012 winner resolves against 2013-14, sidestepping redistricting/turnover skew.

**Depends on #77.** The seat spans bind to existing WSL :class:`Person`s and the era roster is
read from the sponsor archive; both are materialized by the #77 harvest. Until it runs, a
historical winner's Person is absent and its span is skipped (logged ``span_person_absent``) —
correct, not an error. The sponsor provider will fall back to a *live* ``GetSponsors`` pull for
an un-archived biennium (roster only; the Persons still gate the emission).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.provisioning import (
    get_or_create_source as get_or_create_wsl_source,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.span_emit import CitationTarget
from usa_wa_adapter_legislature.sponsor_cohort import SponsorRosterCohortProvider
from usa_wa_adapter_legislature.synthesis import biennium_for_date
from usa_wa_adapter_legislature.tenure_spans import Observation, build_tenure_spans
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_adapter_pdc.adapter import seating_biennium_for_election_year
from usa_wa_adapter_pdc.normalize.pdc_matching import build_house_roster, build_senate_roster
from usa_wa_adapter_pdc.normalize.pdc_observations import (
    build_house_position_observations,
    build_senate_identity_links,
)
from usa_wa_adapter_pdc.normalize.pdc_span_emit import (
    emit_house_position_spans,
    emit_pdc_identifiers,
)
from usa_wa_adapter_pdc.pdc_cohort import PdcWinnerCohortProvider
from usa_wa_adapter_pdc.provisioning import get_or_create_source as get_or_create_pdc_source

logger = get_logger(__name__)


def _roster_member_ids(roster: dict[int, list]) -> set[str]:
    """The set of WSL member ids in a ``{LD: [entry]}`` roster (House or Senate)."""
    return {entry.member_id for entries in roster.values() for entry in entries}


@dataclass
class PdcSpanResult:
    """Counts from one Phase B build."""

    house_spans: int = 0
    identifiers: int = 0
    house_years: int = 0
    senate_years: int = 0
    coverage: dict[int, dict[str, int]] = field(default_factory=dict)


async def build_pdc_spans(
    session: AsyncSession,
    *,
    sponsor_client: WSLClient | None = None,
    current_biennium: str | None = None,
    restrict_to_biennium: str | None = None,
) -> PdcSpanResult:
    """Build + emit era-matched House Position spans + ``person_wa_pdc`` links; return counts.

    ``restrict_to_biennium`` scopes the House emission to members observed in that biennium (the
    daily re-drive passes the current biennium — each scoped member keeps their full span
    history). ``None`` (the harvest path) rebuilds all."""
    jurisdiction = await resolve_jurisdiction(session)
    pdc_source = await get_or_create_pdc_source(session, jurisdiction)
    wsl_source = await get_or_create_wsl_source(session, jurisdiction)
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=current, jurisdiction_id=jurisdiction.id
    )

    cohorts = PdcWinnerCohortProvider(session=session, source_id=pdc_source.id)
    sponsors = SponsorRosterCohortProvider(
        sponsor_client or WSLClient("SponsorService"), session=session, source_id=wsl_source.id
    )
    house_cohorts = await cohorts.house_cohorts()
    senate_cohorts = await cohorts.senate_cohorts()
    if not house_cohorts and not senate_cohorts:
        logger.warning("pdc_span_build_no_archive")
        return PdcSpanResult()
    house_events = await cohorts.house_events()

    era_cache: dict[str, tuple[dict, dict]] = {}

    async def _era_rosters(biennium: str) -> tuple[dict, dict]:
        if biennium not in era_cache:
            rows = await sponsors.cohort(biennium)
            era_cache[biennium] = (build_house_roster(rows), build_senate_roster(rows))
        return era_cache[biennium]

    observations: list[Observation] = []
    identifiers: list[tuple[str, str]] = []
    fetch_events: dict[str, CitationTarget] = {}
    result = PdcSpanResult(house_years=len(house_cohorts), senate_years=len(senate_cohorts))

    # House — era-matched Position observations (merged into spans below) + identifier links.
    for year in sorted(house_cohorts):
        if not house_cohorts[year]:
            continue  # empty cohort → nothing to match, no era roster needed
        biennium = seating_biennium_for_election_year(year)
        house_roster, senate_roster = await _era_rosters(biennium)
        proj = build_house_position_observations(
            house_cohorts[year],
            house_roster=house_roster,
            senate_roster=senate_roster,
            biennium=biennium,
        )
        observations.extend(proj.observations)
        identifiers.extend(proj.pdc_identifiers)
        result.coverage[year] = proj.summary
        # Log the per-cohort coverage so a shortfall (fewer seated than the 98-seat House) is
        # visible — the issue's "log the shortfall", symmetric with the Senate log below.
        logger.info("pdc_house_cohort", extra={"year": year, **proj.summary})
        for member_id, _biennium in proj.inferred_keys:
            logger.info(
                "pdc_house_seat_inferred", extra={"member_id": member_id, "biennium": biennium}
            )
        if year in house_events:
            fetch_events[biennium] = house_events[year]

    # Senate — identifier-only (#75). The staggered senators are all sitting, so the daily
    # re-drive matches them against the **current** roster (``restrict_to_biennium``), not each
    # cohort's historical seating biennium — otherwise the ``start-3`` cohort would force a live
    # ``GetSponsors`` pull for a non-current biennium every day. The backfill (restrict None)
    # era-matches each cohort to who actually won then.
    for year in sorted(senate_cohorts):
        if not senate_cohorts[year]:
            continue  # empty cohort → nothing to match, no era roster needed
        senate_biennium = restrict_to_biennium or seating_biennium_for_election_year(year)
        _house_roster, senate_roster = await _era_rosters(senate_biennium)
        links = build_senate_identity_links(senate_cohorts[year], senate_roster=senate_roster)
        identifiers.extend(links.identifiers)
        logger.info("pdc_senate_cohort", extra={"year": year, **links.summary})

    if restrict_to_biennium is not None:
        # Daily re-drive: keep only current members' House spans (each with full history) and
        # only current members' identifiers — the current biennium's rosters define "current".
        observed = {o.member_id for o in observations if o.biennium == restrict_to_biennium}
        observations = [o for o in observations if o.member_id in observed]
        current_house, current_senate = await _era_rosters(restrict_to_biennium)
        current_ids = _roster_member_ids(current_house) | _roster_member_ids(current_senate)
        identifiers = [(m, p) for (m, p) in identifiers if m in current_ids]

    spans = build_tenure_spans(observations, current_biennium=current)
    result.house_spans = await emit_house_position_spans(
        session,
        spans,
        anchors=anchors,
        reliability=pdc_source.reliability,
        fetch_events=fetch_events,
    )
    result.identifiers = await emit_pdc_identifiers(session, identifiers)
    logger.info(
        "pdc_span_build_complete",
        extra={
            "house_years": result.house_years,
            "senate_years": result.senate_years,
            "house_spans": result.house_spans,
            "identifiers": result.identifiers,
            "restricted": restrict_to_biennium,
        },
    )
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Build era-matched PDC House Position spans + identifiers from archive (#79)."
    )
    parser.add_argument("--dry-run", action="store_true", help="build but roll back (preview)")
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await build_pdc_spans(session)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("pdc_span_build_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"PDC span build: house_spans={result.house_spans} identifiers={result.identifiers} "
        f"house_years={result.house_years} senate_years={result.senate_years} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
