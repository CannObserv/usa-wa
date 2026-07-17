"""WSL+SOS House Position span builder (#101, Phase B) — the re-partition core.

Reads the WSL sponsor roster (who sits — LD + party, archive-first) and the SOS votewa filing
archive (the ballot Position 1/2) **offline**, joins them per biennium into positioned tenure
observations (:mod:`normalize.house_seats`), merges those across biennia into
:class:`~usa_wa_adapter_legislature.tenure_spans.TenureSpan`s, and emits one
**``usa_wa_legislature``-sourced** ``state_representative`` Position seat Assignment per tenure —
symmetric with the Senate seat (#75). No PDC winner cohort: PDC is demoted to the
``person_wa_pdc`` cross-link only (:mod:`usa_wa_adapter_pdc.build_pdc_spans`, identifier-only).

**One builder, one span identity.** The daily re-drive (``restrict_to_biennium`` = current) and
the historical backfill (``restrict_to_biennium=None``) are the same pipeline with the same SOS
positions, so a member serving across the 2018 boundary builds ONE deep span either way — the
#100 CR finding-1 two-builder depth mismatch cannot recur.

**Coverage.** Position 2008→present (the votewa floor); a sitting member with no resolvable SOS
position (pre-2008, or a match miss) gets no House Position seat (OQ1 — a positioned seat's
absence is honest, not a position-less ``state_representative``, which PM rejects). Depends on
#77 (Persons + sponsor archive) and the SOS harvest (#100 Phase A).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from usa_wa_adapter_pdc.adapter import election_year_for_biennium
from usa_wa_adapter_pdc.normalize.pdc_matching import build_house_roster
from usa_wa_adapter_pdc.normalize.pdc_observations import KIND_HOUSE
from usa_wa_adapter_pdc.normalize.pdc_span_emit import emit_house_position_spans

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.provisioning import (
    get_or_create_source as get_or_create_wsl_source,
)
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.span_emit import (
    MAX_CLOSE_FRACTION_DEFAULT,
    CitationTarget,
    close_fraction,
    close_stale_spans,
)
from usa_wa_adapter_legislature.sponsor_cohort import SponsorRosterCohortProvider
from usa_wa_adapter_legislature.synthesis import biennium_for_date
from usa_wa_adapter_legislature.tenure_spans import Observation, build_tenure_spans
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_adapter_sos.normalize.house_seats import build_house_seat_observations
from usa_wa_adapter_sos.provisioning import get_or_create_source as get_or_create_sos_source
from usa_wa_adapter_sos.sos_cohort import SosFilingCohortProvider

logger = get_logger(__name__)

_HOUSE_ASSIGNMENT_SOURCE = "usa_wa_legislature"


@dataclass
class HouseSpanResult:
    """Counts from one WSL+SOS House Position span build."""

    house_spans: int = 0
    bienniums: int = 0
    closed_stale: int = 0
    sweep_aborted: bool = False
    coverage: dict[str, dict[str, int]] = field(default_factory=dict)


async def build_house_position_spans(
    session: AsyncSession,
    *,
    sponsor_client: WSLClient | None = None,
    current_biennium: str | None = None,
    restrict_to_biennium: str | None = None,
    max_close_fraction: float = MAX_CLOSE_FRACTION_DEFAULT,
) -> HouseSpanResult:
    """Build + emit ``usa_wa_legislature`` House Position seat spans; return counts.

    ``restrict_to_biennium`` scopes the emission to members observed in that biennium (the daily
    re-drive passes the current biennium — each scoped member keeps their full span history).
    ``None`` (the historical backfill) rebuilds all archived bienniums."""
    jurisdiction = await resolve_jurisdiction(session)
    wsl_source = await get_or_create_wsl_source(session, jurisdiction)
    sos_source = await get_or_create_sos_source(session, jurisdiction)
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=current, jurisdiction_id=jurisdiction.id
    )

    sponsors = SponsorRosterCohortProvider(
        sponsor_client or WSLClient("SponsorService"), session=session, source_id=wsl_source.id
    )
    sos = SosFilingCohortProvider(session=session, source_id=sos_source.id)
    filings = await sos.house_filings()
    citation_events = await sos.citation_events()
    bienniums = await sponsors.archived_bienniums()

    observations: list[Observation] = []
    fetch_events: dict[str, CitationTarget] = {}
    result = HouseSpanResult(bienniums=len(bienniums))
    for biennium in bienniums:
        election_year = election_year_for_biennium(biennium)
        house_roster = build_house_roster(await sponsors.cohort(biennium))
        proj = build_house_seat_observations(
            house_roster, filings.get(election_year, {}), biennium=biennium
        )
        observations.extend(proj.observations)
        result.coverage[biennium] = proj.summary
        logger.info("house_seat_cohort", extra={"biennium": biennium, **proj.summary})
        event = citation_events.get(election_year)
        if event is not None:
            fetch_events[biennium] = event

    if restrict_to_biennium is not None:
        observed = {o.member_id for o in observations if o.biennium == restrict_to_biennium}
        observations = [o for o in observations if o.member_id in observed]

    spans = build_tenure_spans(observations, current_biennium=current)
    result.house_spans = await emit_house_position_spans(
        session,
        spans,
        anchors=anchors,
        reliability=sos_source.reliability,
        fetch_events=fetch_events,
        assignment_source=_HOUSE_ASSIGNMENT_SOURCE,
    )
    # #83: a departed member keeps no observation in the (possibly restricted) rebuilt set, so
    # their open chamber-house span would stay is_active forever — close it.
    sweep = await close_stale_spans(
        session,
        assignment_source=_HOUSE_ASSIGNMENT_SOURCE,
        kinds={KIND_HOUSE},
        asserted_source_ids={s.source_id for s in spans},
        current_biennium=current,
        max_close_fraction=max_close_fraction,
    )
    result.closed_stale = sweep.closed
    result.sweep_aborted = sweep.aborted
    logger.info(
        "house_span_build_complete",
        extra={
            "bienniums": result.bienniums,
            "house_spans": result.house_spans,
            "closed_stale": sweep.closed,
            "sweep_aborted": sweep.aborted,
            "restricted": restrict_to_biennium,
        },
    )
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Build WSL+SOS House Position seat spans from archive (#101)."
    )
    parser.add_argument("--dry-run", action="store_true", help="build but roll back (preview)")
    parser.add_argument(
        "--biennium",
        default=None,
        help="the current operating biennium (e.g. 2025-26): scope the rebuild to its members "
        "(each keeps full span history) AND treat it as the span open-end / stale-close "
        "boundary. Omit for a full historical rebuild",
    )
    parser.add_argument(
        "--max-close-fraction",
        type=close_fraction,
        default=MAX_CLOSE_FRACTION_DEFAULT,
        help="mass-close guard ceiling in (0, 1] (#83); 1.0 disables the guard",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await build_house_position_spans(
                session,
                current_biennium=args.biennium,
                restrict_to_biennium=args.biennium,
                max_close_fraction=args.max_close_fraction,
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("house_span_build_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"House Position span build: house_spans={result.house_spans} "
        f"bienniums={result.bienniums} closed_stale={result.closed_stale} "
        f"sweep_aborted={result.sweep_aborted} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
