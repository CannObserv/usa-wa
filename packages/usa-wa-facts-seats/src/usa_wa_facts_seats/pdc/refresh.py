"""WA PDC refresh — ``python -m usa_wa_facts_seats.pdc.refresh``.

Daily counterpart to the WSL refresh, **identifier-only since #101**. Since #201 it does exactly
one thing: re-drive the archive-first identifier builder (:func:`build_pdc_spans`) scoped to the
current biennium — emitting the ``person_wa_pdc`` cross-source identifier links (House winners +
the #74 movers + the #75 Senate cohort), era-matched.

**The archive half moved to the source (#201).** Archiving the ``house-winners:<Y>`` /
``senate-winners:<Y>`` cohorts is :mod:`usa_wa_adapter_pdc.archive_refresh`
(``usa-wa-pdc-archive-refresh.service``), ordered before this unit. Running both here made this
fact import an adapter ``transport``; the rebuild consumes cohort *interfaces* and still runs
usefully off the last good archive when the source is down.

**The House Position seat is no longer PDC's (#101).** It is built by the WSL+SOS builder
(:func:`usa_wa_facts_seats.house.build.build_house_position_spans`,
``usa_wa_legislature``-sourced, symmetric with the Senate seat), driven daily by the SOS refresh.
PDC is demoted to the identifier link only — which removes the #100 CR finding-1 two-builder
depth mismatch (this refresh no longer rebuilds a shallow ``usa_wa_pdc`` House span for a sweep
to close). The era roster comes archive-first from the WSL sponsor archive (``sponsors:<biennium>``,
written by the WSL refresh, which runs first); a live ``GetSponsors`` fallback covers an
un-archived biennium. Runs **after** the WSL refresh so the Persons it binds to exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.sponsors.cohort import SponsorClient
from usa_wa_facts_seats.pdc.build_pdc_spans import build_pdc_spans

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
#: Unchanged by the #201 split (the archive half took a new slug rather than forking this one).
JOB_SLUG = "pdc-refresh"


@dataclass(frozen=True)
class PdcRefreshOutcome:
    """Counts from one PDC refresh cycle (identifier-only since #101)."""

    identifiers: int


async def run_refresh(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    sponsor_client: SponsorClient | None = None,
) -> PdcRefreshOutcome:
    """Re-drive the identifier builder scoped to the current biennium, reading both cohorts
    archive-first. ``sponsor_client`` is injectable for tests — typed by the cohort provider's
    structural Protocol since #189, not by a SOAP transport."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    current = biennium_for_date(datetime.now(UTC).date())
    if biennium != current:
        logger.warning(
            "pdc_refresh_noncurrent_biennium",
            extra={"biennium": biennium, "current_biennium": current},
        )

    # #101: identifier-only — the House Position seat is the WSL+SOS builder's, driven by the
    # SOS refresh; PDC emits only the person_wa_pdc cross-links here.
    result = await build_pdc_spans(
        session,
        sponsor_client=sponsor_client,
        restrict_to_biennium=biennium,
    )
    logger.info(
        "pdc_refresh_complete",
        extra={"biennium": biennium, "identifiers": result.identifiers},
    )
    return PdcRefreshOutcome(identifiers=result.identifiers)


async def _refresh_job(ctx: JobContext) -> PdcRefreshOutcome:
    """Harness handler, keeping the explicit ``session.begin()`` (``commit=False``) — the
    pre-#179b CLI committed unconditionally through it and had no ``--dry-run``."""
    session = ctx.require_session()
    async with session.begin():
        return await run_refresh(session)


def main(argv: list[str] | None = None) -> int:
    """Run one PDC refresh cycle. Exit ``0`` clean · ``1`` failed · ``2`` config.

    **No ``--dry-run``** (``dry_run=False``, CR #196 finding 55) — the twin of the SOS
    refresh: its own ``session.begin()`` commits regardless, so the flag could only have
    promised a rollback. **No ``--force``** either (#201): the TTL bypass belongs to the
    archive half, which is the only one holding a cache.
    """
    return run_job(
        JOB_SLUG,
        _refresh_job,
        argv=argv,
        prog="python -m usa_wa_facts_seats.pdc.refresh",
        description="Re-drive the PDC identifier links from the archive (#201).",
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
