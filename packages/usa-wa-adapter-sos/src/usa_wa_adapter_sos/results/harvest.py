"""Phase A results harvester (#101) — archive ``results.vote.wa.gov`` legislative cohorts.

For **every** general-election year in a range, archive the ``sos-legresults:<YYYYMMDD>`` CSV
cohort through :meth:`~clearinghouse_core.runner.AdapterRunner.archive_only` — pristine wire +
#54 hash, no normalize. Phase B (:mod:`usa_wa_adapter_sos.results.cohort`) derives the House
Position and the Senate winners from this archive offline.

**Odd years included (#106).** WA holds a general each November and an odd-year general seats
legislators via specials — Nov 2025 elected Hunt to the LD5 Senate and four House appointees whose
seats otherwise rest on the #103 elimination inference. An even-only sweep never archived that
ballot evidence. Probed live: every odd-year export index 2009→2025 exists; 2021 and 2023 ran no
legislative race and so carry no Legislative CSV.

**Per-year resilient.** A year the source 404s/500s (an unheld year, an outage) or that carries no
Legislative CSV (:class:`LegislativeExportNotFound`) is **skipped-and-logged** inside its own
SAVEPOINT, and the years the sweep *reached* still commit — unlike an all-or-nothing sweep that
rolls the whole run back on one bad year. This source genuinely needs it: filenames vary per year
(discovered via ``export.html``) and future years 404 until held. The two are tallied apart:
no-legislative-race is expected (``cohorts_absent``), an HTTP failure is not (``cohorts_skipped``,
and the only one that can raise the whole-source outage warning).

Floor **2008** (the PDC winner floor + the earliest results this fills against).

    python -m usa_wa_adapter_sos.results.harvest --from-year 2008 --to-year 2025 [--dry-run]

The **daily** Phase-A driver lives beside it (:mod:`usa_wa_adapter_sos.results.archive_refresh`,
#201) and drives :func:`harvest_results` over the current biennium's two cohorts with
``force=True``; ``expected_years`` is the one behavioural difference it needs.
"""

from __future__ import annotations

import argparse
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_sos.coverage import SOS_RESULTS_ELECTION_YEARS
from usa_wa_adapter_sos.provisioning import get_or_create_results_source
from usa_wa_adapter_sos.results.adapter import ResultsAdapter, legresults_resource_id
from usa_wa_adapter_sos.results.transport import (
    LegislativeExportNotFound,
    SOSResultsClient,
    configure_results_rate_limit,
)
from usa_wa_common.jurisdiction import resolve_jurisdiction

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "sos-results-harvest"

#: The earliest general-election year this source fills against (the PDC winner floor) — the
#: declared coverage claim (#180).
DEFAULT_ELECTION_FLOOR = SOS_RESULTS_ELECTION_YEARS.floor_year


@dataclass(frozen=True)
class HarvestSummary:
    """Counts from one Phase A sweep.

    ``cohorts_absent`` and ``cohorts_skipped`` are deliberately distinct (#106): a general the
    state held but that ran **no** legislative race carries no Legislative CSV — an expected
    outcome of sweeping odd years, not a source failure.
    """

    years: int
    cohorts_archived: int
    cohorts_absent: int
    cohorts_skipped: int
    dry_run: bool


def general_election_years(from_year: int, to_year: int) -> list[int]:
    """Inclusive general-election years from ``from_year`` to ``to_year`` — **every** year, not
    only even ones (#106).

    WA holds a general election each November, and an odd-year general seats legislators via
    specials (Nov 2025: Hunt to the LD5 Senate; Obras / Salahuddin / Zahn / Thomas to the House).
    An even-only sweep never archived their ballot evidence. An odd year with no legislative race
    simply carries no Legislative CSV and is counted ``cohorts_absent``."""
    return list(range(from_year, to_year + 1))


async def harvest_results(
    session: AsyncSession,
    *,
    years: list[int],
    results_client: SOSResultsClient | None = None,
    dry_run: bool = False,
    force: bool = False,
    expected_years: Collection[int] | None = None,
) -> HarvestSummary:
    """Archive each year's legislative-results cohort (archive-only), **per-year resilient**.

    Each year runs in its own SAVEPOINT: any source-side HTTP failure — a status error (an unheld
    year 404s, an outage 500s), a **transport error** (connect/read timeout, reset — the likeliest
    outage symptom against a low-QPS government host), or a missing Legislative CSV — rolls back
    *that year*; the reached years persist. Operates in the caller's transaction (the CLI commits,
    or rolls back on ``dry_run``).

    The two failure modes are tallied apart (#106). A **missing Legislative CSV** means the state
    held that general but ran no legislative race (2021 + 2023 — no specials) → ``cohorts_absent``,
    an expected outcome of the odd-year sweep. Only an **HTTP failure** — the source couldn't serve
    a year it should have — counts ``cohorts_skipped`` and can raise the whole-source signal.

    ``expected_years`` names the years whose HTTP failure is a genuine **WARNING**; any other
    year's is INFO. It exists for the daily archive refresh (#201), which sweeps the current
    biennium every morning: that biennium's odd cohort 404s from January until November's
    election is certified, so warning on it would page the operator for eleven months (this
    project alerts on WARNING rises, #85). Default ``None`` = every year is expected, which is
    the historical sweep's semantics — a closed year that fails to serve is always a warning."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_results_source(session, jurisdiction)
    adapter = ResultsAdapter(election_years=years, client=results_client or SOSResultsClient())
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )

    archived = absent = skipped = 0
    for year in years:
        try:
            async with session.begin_nested():
                if await runner.archive_only(legresults_resource_id(year), force=force):
                    archived += 1
            logger.info("results_cohort_year_harvested", extra={"year": year})
        except LegislativeExportNotFound as exc:
            # The general was held but ran no legislative race — expected in an odd year (#106).
            absent += 1
            logger.info(
                "results_cohort_year_no_legislative_race", extra={"year": year, "error": str(exc)}
            )
        except httpx.HTTPError as exc:
            # httpx.HTTPError is the common base of HTTPStatusError (4xx/5xx) and TransportError
            # (timeouts/connect resets): both mean the source couldn't serve this year, so skip
            # the year not the sweep. A DB/SQLAlchemy error is not an httpx error, so it aborts.
            skipped += 1
            log = (
                logger.warning if expected_years is None or year in expected_years else logger.info
            )
            log("results_cohort_year_skipped", extra={"year": year, "error": str(exc)})

    if skipped > 0 and skipped == len(years):
        # *Every* year failed — a whole-source outage, not one bad year in a good run. A sweep of
        # only race-less years (all ``absent``) is not an outage and deliberately stays quiet.
        #
        # The test is "all years skipped", not "archived == 0" (CR #196 finding 23):
        # ``archive_only`` returns False on a **cache hit**, so ``archived`` counts fetches, not
        # successes, and a sweep where the served years all cache-hit and one year fails would
        # otherwise report a whole-source outage.
        logger.warning(
            "results_harvest_total_outage", extra={"years": len(years), "skipped": skipped}
        )

    return HarvestSummary(
        years=len(years),
        cohorts_archived=archived,
        cohorts_absent=absent,
        cohorts_skipped=skipped,
        dry_run=dry_run,
    )


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the sweep's own flags to the harness's shared parser."""
    parser.add_argument(
        "--from-year",
        type=int,
        default=DEFAULT_ELECTION_FLOOR,
        help=f"earliest general-election year (default {DEFAULT_ELECTION_FLOOR})",
    )
    parser.add_argument(
        "--to-year", type=int, default=None, help="default: the current calendar year"
    )
    parser.add_argument("--force", action="store_true", help="re-fetch past the freshness cache")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=None,
        help=(
            "central results.vote.wa.gov min-interval between calls (courtesy); unset leaves the "
            "value seeded from USA_WA_SOS_RESULTS_MIN_REQUEST_INTERVAL (default 1.0) in place"
        ),
    )


async def _harvest_job(ctx: JobContext) -> JobResult:
    """Harness handler: resolve the range, sweep, and grade the outcome.

    **A whole-source outage is now ``degraded``, not ``ok`` (#179b, CR #196 finding 22.)**
    ``harvest_results`` already detected the condition and logged
    ``results_harvest_total_outage``; the CLI then returned ``0``, so the warning had no
    consumer — the "exits 0 having done nothing" failure ``clearinghouse_core.runs`` was
    built for, in the module its own docstring names as the example. The sibling
    ``filings.harvest`` already exited :data:`~clearinghouse_core.job.EXIT_DEGRADED` for
    the identical condition; this brings the two halves of one source into line.

    The test is skipped-vs-total, not ``archived == 0``: ``archive_only`` returns False on
    a cache hit, so a sweep whose served years all cache-hit is not an outage. An
    absent-only sweep (a general with no legislative race, #106) is not one either.
    ``degraded`` still commits, so the reached years persist.
    """
    args = ctx.args
    # Only override the central limiter when the operator asked (#169) — an unconditional call
    # let the flag's own default silently overwrite the env-seeded interval.
    if args.pause_seconds is not None:
        configure_results_rate_limit(args.pause_seconds)
    # The current *calendar* year, not the biennium's seating election year (#106): in 2025-26 the
    # latter is 2024, so defaulting to it would stop the sweep short of the very odd-year cohort
    # this harvest exists to archive. A year not yet held simply 404s and is skipped-and-logged.
    to_year = args.to_year or datetime.now(UTC).year
    years = general_election_years(args.from_year, to_year)
    summary = await harvest_results(
        ctx.require_session(), years=years, dry_run=ctx.dry_run, force=args.force
    )
    if summary.cohorts_skipped > 0 and summary.cohorts_skipped == summary.years:
        return JobResult.degraded(summary)
    return JobResult.ok(summary)


def main(argv: list[str] | None = None) -> int:
    """Archive the legislative results cohorts.

    Exit ``0`` clean · ``1`` failed · ``2`` config · ``4``
    (:data:`~clearinghouse_core.job.EXIT_DEGRADED`) a whole-source outage. The ``4`` is
    new at #179b — see :func:`_harvest_job`.
    """
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_sos.results.harvest",
        description=(
            "Archive results.vote.wa.gov legislative cohorts (archive-only, #101 Phase A)."
        ),
        extra_args=_add_args,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
