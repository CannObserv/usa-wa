"""Phase B SOS-fed House Position span build (#100).

Runs the PDC span builder (:func:`usa_wa_adapter_pdc.build_pdc_spans.build_pdc_spans`) with the
votewa **position fallback** injected, so a pre-2018 House winner PDC couldn't position (its
dataset omitted the field) is seated at the ballot ``Position`` the SOS filing archive supplies.

This is the single coherent House rebuild: PDC positions where present (2018+), SOS fallback
where absent (2008–2016). PDC stays the winner authority; SOS contributes only the qualifier.
Dependency stays one-directional — the PDC builder takes an injected callable and never imports
this package; this module wires the SOS provider into it.

Depends on the SOS archive (:mod:`harvest_sos`), the PDC winner archive (``harvest_pdc``), and
the WSL sponsor archive + Persons (#77). Run it in the same window as those harvests, sidecar
paused (a freshly-materialized span the sidecar sees first mints its own PM assignment).

    python -m usa_wa_adapter_sos.build_sos_house_spans [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from usa_wa_adapter_pdc.build_pdc_spans import PdcSpanResult, build_pdc_spans

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction
from usa_wa_adapter_legislature.span_emit import (
    MAX_CLOSE_FRACTION_DEFAULT,
    close_fraction,
)
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_adapter_sos.provisioning import get_or_create_source
from usa_wa_adapter_sos.sos_cohort import SosFilingCohortProvider

logger = get_logger(__name__)


async def build_sos_house_spans(
    session: AsyncSession,
    *,
    sponsor_client: WSLClient | None = None,
    current_biennium: str | None = None,
    restrict_to_biennium: str | None = None,
    max_close_fraction: float = MAX_CLOSE_FRACTION_DEFAULT,
) -> PdcSpanResult:
    """Build House Position spans with the votewa fallback wired into the PDC builder (#100)."""
    jurisdiction = await resolve_jurisdiction(session)
    sos_source = await get_or_create_source(session, jurisdiction)
    provider = SosFilingCohortProvider(session=session, source_id=sos_source.id)
    fallback_factory = await provider.fallback_factory()
    result = await build_pdc_spans(
        session,
        sponsor_client=sponsor_client,
        current_biennium=current_biennium,
        restrict_to_biennium=restrict_to_biennium,
        max_close_fraction=max_close_fraction,
        house_position_fallback=fallback_factory,
    )
    logger.info(
        "sos_house_span_build_complete",
        extra={"house_spans": result.house_spans, "coverage_years": len(result.coverage)},
    )
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Build House Position spans with the votewa position fallback (#100 Phase B)."
    )
    parser.add_argument("--dry-run", action="store_true", help="build but roll back (preview)")
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
            result = await build_sos_house_spans(
                session, max_close_fraction=args.max_close_fraction
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("sos_house_span_build_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"SOS House span build: house_spans={result.house_spans} "
        f"identifiers={result.identifiers} closed_stale={result.closed_stale} "
        f"sweep_aborted={result.sweep_aborted} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
