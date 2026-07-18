"""Phase B PDC builder (#79, **identifier-only since #101**) — archive → ``person_wa_pdc`` links.

Reads every archived ``house-winners:<Y>`` / ``senate-winners:<Y>`` cohort **offline** (via
:class:`~usa_wa_adapter_pdc.pdc_cohort.PdcWinnerCohortProvider`), pairs each with the roster of
the biennium it **seated** (``[Y+1, Y+2]``, read archive-first from the WSL sponsor archive),
matches each winner to a WSL :class:`Person`, and emits the ``person_wa_pdc`` cross-source
identifier links (House winners + the #74 mid-biennium movers + the #75 Senate cohort).

**PDC is identifier-only (#101).** The House Position **seat** is now built by the WSL+SOS
builder (:func:`usa_wa_adapter_sos.build_house_spans.build_house_position_spans`,
``usa_wa_legislature``-sourced, symmetric with the Senate seat) — PDC no longer emits or sweeps
House Position Assignments. This builder keeps only PDC's demoted contribution: the
``person_wa_pdc`` identifier linking a WSL Person to their PDC filer id. Retiring the House span
emission is the #101 fix for the #100 CR finding-1 two-builder depth mismatch (the daily PDC
refresh no longer rebuilds a shallow ``usa_wa_pdc`` House span that a sweep would then close).

**The #75 fix.** Each cohort matches the roster of its *own* era, not the current one — so a
2012 winner resolves against 2013-14, sidestepping redistricting/turnover skew.

**Depends on #77.** The links bind to existing WSL :class:`Person`s; until the #77 harvest runs,
a historical winner's Person is absent and its link is skipped (logged), correct. The sponsor
provider falls back to a *live* ``GetSponsors`` pull for an un-archived biennium.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.provisioning import (
    get_or_create_source as get_or_create_wsl_source,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.sponsor_cohort import SponsorRosterCohortProvider
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_adapter_pdc.adapter import seating_biennium_for_election_year
from usa_wa_adapter_pdc.normalize.pdc_matching import (
    HouseRosterEntry,
    SenateEntry,
    build_house_roster,
    build_senate_roster,
)
from usa_wa_adapter_pdc.normalize.pdc_observations import (
    build_house_position_observations,
    build_senate_identity_links,
)
from usa_wa_adapter_pdc.normalize.pdc_span_emit import emit_pdc_identifiers
from usa_wa_adapter_pdc.pdc_cohort import PdcWinnerCohortProvider
from usa_wa_adapter_pdc.provisioning import get_or_create_source as get_or_create_pdc_source

logger = get_logger(__name__)


def _roster_member_ids(
    roster: dict[int, list[HouseRosterEntry]] | dict[int, list[SenateEntry]],
) -> set[str]:
    """The set of WSL member ids in a ``{LD: [entry]}`` roster (House or Senate)."""
    return {entry.member_id for entries in roster.values() for entry in entries}


@dataclass
class PdcSpanResult:
    """Counts from one Phase B build (identifier-only since #101)."""

    identifiers: int = 0
    house_years: int = 0
    senate_years: int = 0
    coverage: dict[int, dict[str, int]] = field(default_factory=dict)


async def build_pdc_spans(
    session: AsyncSession,
    *,
    sponsor_client: WSLClient | None = None,
    restrict_to_biennium: str | None = None,
) -> PdcSpanResult:
    """Emit era-matched ``person_wa_pdc`` identifier links; return counts (identifier-only #101).

    ``restrict_to_biennium`` scopes the links to members observed in that biennium (the daily
    re-drive passes the current biennium). ``None`` (the harvest path) rebuilds all.

    The House match runs PDC-only (a position-less pre-2018 winner is ``incomplete`` → no link);
    a pre-2018 ``person_wa_pdc`` backfill that needs the SOS ballot position to match must re-add
    the SOS→PDC injection retired with ``build_sos_house_spans`` (#101; deferred follow-up)."""
    jurisdiction = await resolve_jurisdiction(session)
    pdc_source = await get_or_create_pdc_source(session, jurisdiction)
    wsl_source = await get_or_create_wsl_source(session, jurisdiction)

    cohorts = PdcWinnerCohortProvider(session=session, source_id=pdc_source.id)
    sponsors = SponsorRosterCohortProvider(
        sponsor_client or WSLClient("SponsorService"), session=session, source_id=wsl_source.id
    )
    house_cohorts = await cohorts.house_cohorts()
    senate_cohorts = await cohorts.senate_cohorts()
    if not house_cohorts and not senate_cohorts:
        logger.warning("pdc_span_build_no_archive")
        return PdcSpanResult()

    era_cache: dict[str, tuple[dict, dict]] = {}

    async def _era_rosters(biennium: str) -> tuple[dict, dict]:
        if biennium not in era_cache:
            rows = await sponsors.cohort(biennium)
            era_cache[biennium] = (build_house_roster(rows), build_senate_roster(rows))
        return era_cache[biennium]

    identifiers: list[tuple[str, str]] = []
    result = PdcSpanResult(house_years=len(house_cohorts), senate_years=len(senate_cohorts))

    # House — era-matched winner→member match, kept ONLY for the person_wa_pdc identifier links
    # (#101 identifier-only; the observations the projector also builds are discarded — the seat
    # is now the WSL+SOS builder's, not PDC's). The #74 mid-biennium movers still cross-link.
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
        identifiers.extend(proj.pdc_identifiers)
        result.coverage[year] = proj.summary
        logger.info("pdc_house_cohort", extra={"year": year, **proj.summary})

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
        # Daily re-drive: keep only current members' identifiers — the current biennium's rosters
        # define "current".
        current_house, current_senate = await _era_rosters(restrict_to_biennium)
        current_ids = _roster_member_ids(current_house) | _roster_member_ids(current_senate)
        identifiers = [(m, p) for (m, p) in identifiers if m in current_ids]

    result.identifiers = await emit_pdc_identifiers(session, identifiers)
    logger.info(
        "pdc_span_build_complete",
        extra={
            "house_years": result.house_years,
            "senate_years": result.senate_years,
            "identifiers": result.identifiers,
            "restricted": restrict_to_biennium,
        },
    )
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Emit era-matched PDC person_wa_pdc identifier links from archive "
        "(#79; identifier-only since #101)."
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
        f"PDC identifier build: identifiers={result.identifiers} "
        f"house_years={result.house_years} senate_years={result.senate_years} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
