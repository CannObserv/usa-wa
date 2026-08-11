"""WA SOS refresh — ``python -m usa_wa_facts_seats.house.refresh`` (#101).

The daily driver of the **WSL+SOS House Position seat** (symmetric with the Senate seat, #75).
Since #201 it does exactly one thing: re-drive the archive-first House-Position span builder
(:func:`usa_wa_facts_seats.house.build.build_house_position_spans`) scoped to the current
biennium — materializing ``usa_wa_legislature`` ``state_representative`` Position seat spans
(the current biennium as the open end).

**The archive half moved to the source (#201).** Archiving the ``sos-legresults:<YYYYMMDD>``
cohorts is :mod:`usa_wa_adapter_sos.results.archive_refresh`
(``usa-wa-sos-archive-refresh.service``), ordered before this unit. Running both in one process
made this fact import an adapter ``transport``, which is the thing a fact must never do — it
re-welds the application to one source, the failure the 2026-07 votewa outage taught. The
rebuild consumes cohort *interfaces*, so it is source-agnostic and, crucially, still useful when
the archive half failed: it re-derives the seat from the **last good** archive while continuing
to track the WSL roster, which votewa has no part in.

**Ordering.** Runs **after** the WSL refresh: the sitting House roster (who sits / LD / party) is
read archive-first from the WSL sponsor archive (``sponsors:<biennium>``, written by the WSL
refresh), and the seat binds to WSL-sourced :class:`Person`s. Independent of the PDC refresh (PDC
is identifier-only since #101). A live ``GetSponsors`` fallback covers an un-archived biennium.

This is the daily counterpart of the historical House backfill (the same builder with
``restrict_to_biennium=None``): **one builder → the #100 CR finding-1 depth mismatch cannot
recur** (a cross-2018 member builds the same deep span daily and historically).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.membership.cohort import MemberClient
from usa_wa_adapter_legislature.sponsors.cohort import SponsorClient
from usa_wa_facts_seats.house.build import build_house_position_spans

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
#: Unchanged by the #201 split: this half kept the seat the unit exists to materialize, and
#: the archive half took a new slug rather than forking this one's history.
JOB_SLUG = "sos-refresh"


@dataclass(frozen=True)
class SosRefreshOutcome:
    """Counts from one SOS refresh cycle."""

    house_spans: int


async def run_refresh(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    sponsor_client: SponsorClient | None = None,
    member_client: MemberClient | None = None,
) -> SosRefreshOutcome:
    """Re-drive the House-Position span builder scoped to the current biennium, reading every
    cohort archive-first. ``sponsor_client`` / ``member_client`` are injectable for tests —
    typed by the cohort providers' structural Protocols since #189, so no transport is named
    here (and since #201, none is imported either)."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    current = biennium_for_date(datetime.now(UTC).date())
    if biennium != current:
        logger.warning(
            "sos_refresh_noncurrent_biennium",
            extra={"biennium": biennium, "current_biennium": current},
        )

    # Each scoped member keeps their full cross-biennium span history; the current biennium is
    # the open end.
    result = await build_house_position_spans(
        session,
        sponsor_client=sponsor_client,
        member_client=member_client,
        current_biennium=biennium,
        restrict_to_biennium=biennium,
    )
    logger.info(
        "sos_refresh_complete",
        extra={
            "biennium": biennium,
            "house_spans": result.house_spans,
            "closed_stale": result.closed_stale,
            "sweep_aborted": result.sweep_aborted,
        },
    )
    return SosRefreshOutcome(house_spans=result.house_spans)


async def _refresh_job(ctx: JobContext) -> SosRefreshOutcome:
    """Harness handler, keeping the explicit ``session.begin()`` (``commit=False``).

    The pre-#179b CLI committed unconditionally through that block and had no
    ``--dry-run``; leaving the transaction here keeps that exactly.
    """
    session = ctx.require_session()
    async with session.begin():
        return await run_refresh(session)


def main(argv: list[str] | None = None) -> int:
    """Run one SOS refresh cycle. Exit ``0`` clean · ``1`` failed · ``2`` config.

    **No ``--dry-run``** (``dry_run=False``, CR #196 finding 55). This commits through its
    own ``session.begin()`` regardless, so the flag would have re-driven the House builder,
    committed, and reported ``dry_run=true``. It had no such flag before #179b; the sweep
    added it and nothing read it.

    **No ``--force``** either (#201): forcing past a freshness TTL is the archive half's
    business, and this half holds no cache — the builder is idempotent and re-derives from
    the archive every run.
    """
    return run_job(
        JOB_SLUG,
        _refresh_job,
        argv=argv,
        prog="python -m usa_wa_facts_seats.house.refresh",
        description="Re-drive the House Position span builder from the archive (#201).",
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
