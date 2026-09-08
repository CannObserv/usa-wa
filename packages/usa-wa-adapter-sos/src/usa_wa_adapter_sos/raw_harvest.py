"""SOS raw-tier harvest (#304): filings + results wires into the file store.

    python -m usa_wa_adapter_sos.raw_harvest [--root PATH] [--ttl-days N]

The file-store sibling of the two Postgres-tier Phase-A jobs
(``sos-filings-harvest`` / ``sos-archive-refresh``), feeding the #302
pipeline: for each election year seating the biennium, the WhoFiled filings
export and the legislative results export, written as pristine wires under the
same resource ids the Postgres archive uses (``sos-whofiled:<date>`` /
``sos-legresults:<date>``) into their own source slices (``usa_wa_sos`` /
``usa_wa_sos_results``). Per-cohort and per-source failures are contained as
``err`` manifest entries; both stores close their run manifests regardless.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import RawStore, get_raw_root, record_fetch
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_sos.filings.adapter import whofiled_resource_id
from usa_wa_adapter_sos.filings.transport import (
    SOSFilingsClient,
)
from usa_wa_adapter_sos.filings.transport import (
    general_election_date as filings_election_date,
)
from usa_wa_adapter_sos.provisioning import RESULTS_SOURCE_SLUG, SOS_SOURCE_SLUG
from usa_wa_adapter_sos.results.adapter import legresults_resource_id
from usa_wa_adapter_sos.results.transport import (
    SOSResultsClient,
)
from usa_wa_adapter_sos.results.transport import (
    general_election_date as results_election_date,
)
from usa_wa_common.elections import election_years_for_biennium

logger = get_logger(__name__)

#: Stable ledger identity (#178); distinct from the Postgres-tier SOS jobs.
JOB_SLUG = "sos-raw-harvest"


@dataclass(frozen=True)
class AcceptedOutage:
    """A source whose total failure is KNOWN, tracked, and not worth an alert.

    ``source`` is ``filings`` or ``results`` — the same granularity
    :func:`_source_degraded` works at, and deliberately no finer. The counters
    record that a source landed nothing; they do not carry the reason, so this
    cannot be narrowed to a particular HTTP status without plumbing error
    detail through the whole harvest. The consequence is stated rather than
    hidden: while an acceptance stands, a *different* cause of total failure in
    that same source is also accepted. What bounds it is the staleness rule —
    the acceptance survives only as long as the source keeps landing nothing.
    """

    source: str
    reason: str
    issue: str
    #: When the outage was first observed. Not enforced — an acceptance expires
    #: by the source RECOVERING, not by the calendar — but it dates the claim so
    #: a reader can see how long this has been standing.
    observed: str


#: The outages currently accepted. Empty is the normal state.
#:
#: This is the ``parity_wsl.ACCEPTED`` idiom: an exemption lives in code with a
#: named reason and an issue, and goes stale loudly. Alert fatigue is how #49
#: alerting dies (see ``parity_registry``), and a nightly email nobody can act
#: on is how it starts.
ACCEPTED_OUTAGES: tuple[AcceptedOutage, ...] = (
    AcceptedOutage(
        source="filings",
        reason=(
            "votewa.gov's WhoFiled ExportToExcel endpoint returns HTTP 500 for every "
            "election date. Nothing downstream reads the filings source — stg_sos_filings "
            "is published but feeds no span, citation or conformed product — so the "
            "outage costs coverage this deployment does not yet use."
        ),
        issue="#333",
        observed="2026-09-03",
    ),
)


async def harvest_raw(
    root: Path | str,
    *,
    biennium: str | None = None,
    filings_client: Any | None = None,
    results_client: Any | None = None,
    ttl_days: float = 0.0,
) -> dict[str, int]:
    """Fetch the biennium's filings + results wires into the raw store."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    filings = filings_client or SOSFilingsClient()
    results = results_client or SOSResultsClient()
    years = election_years_for_biennium(biennium)
    # Per-source counters (#302 CR): a total filings outage must not be masked
    # by healthy results — the sibling Postgres-tier jobs alert per source.
    filings_counters = {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 0}
    results_counters = {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 0}
    # from the clients actually fetching when they can say (CR 45)
    filings_url_source = filings if hasattr(filings, "export_url") else SOSFilingsClient()
    filings_url = filings_url_source.export_url()
    results_url_source = results if hasattr(results, "export_index_url") else SOSResultsClient()

    filings_store = RawStore(root, SOS_SOURCE_SLUG)
    filings_run = filings_store.open_run()
    try:
        for year in years:
            params = SOSFilingsClient.whofiled_params(filings_election_date(year))
            await record_fetch(
                filings_run,
                filings_store,
                whofiled_resource_id(year),
                # the real, replayable request (#54 provenance) — not a fabricated URL
                f"{filings_url}?{urlencode(params)}",
                lambda y=year: filings.fetch_whofiled(y),
                filings_counters,
                ttl_days,
                log_event="sos_raw_harvest_cohort_failed",
            )
    finally:
        filings_run.close()

    results_store = RawStore(root, RESULTS_SOURCE_SLUG)
    results_run = results_store.open_run()
    try:
        for year in years:
            await record_fetch(
                results_run,
                results_store,
                legresults_resource_id(year),
                # the export index the traversal starts from — computable and real
                results_url_source.export_index_url(results_election_date(year)),
                lambda y=year: results.fetch_legislative_results(y),
                results_counters,
                ttl_days,
                log_event="sos_raw_harvest_cohort_failed",
            )
    finally:
        results_run.close()

    counters: dict[str, Any] = {
        key: filings_counters[key] + results_counters[key] for key in filings_counters
    }
    counters["filings"] = filings_counters
    counters["results"] = results_counters
    logger.info("sos_raw_harvest_complete", extra={"biennium": biennium, "summary": counters})
    return counters


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")
    parser.add_argument(
        "--ttl-days",
        type=float,
        default=0.0,
        help="Skip resources fetched ok within N days (0 = always fetch, the daily default).",
    )


def _source_degraded(source: dict[str, int]) -> bool:
    landed_nothing = source["fetched"] == 0 and source["skipped_fresh"] == 0
    every_attempt_failed = source["errors"] > 0 and source["fetched"] == 0
    return landed_nothing or every_attempt_failed


def job_outcome(
    counters: dict[str, Any],
    *,
    accepted: tuple[AcceptedOutage, ...] = ACCEPTED_OUTAGES,
) -> JobResult:
    """Degraded when EITHER source landed nothing or lost every attempted fetch
    (#302 CR): per-source outage semantics, matching ``sos-filings-harvest``.

    An outage named in ``accepted`` does not degrade the run — it is counted in
    ``accepted_outages`` and logged at WARNING, so it stays legible in the
    journal while the nightly stops mailing about it (#333).

    **An acceptance that is no longer needed degrades the run instead.** A named
    source that is healthy again means the code now asserts something false, so
    it says so once, in ``stale_acceptances``, which is what forces its removal.
    Without that half the exemption outlives the outage and the source can go
    dark with nothing left to notice.
    """
    degraded = {name for name in ("filings", "results") if _source_degraded(counters[name])}
    named = {outage.source for outage in accepted}
    stale = sorted(named - degraded)
    unaccepted = sorted(degraded - named)
    reported = dict(counters)
    reported["accepted_outages"] = sorted(degraded & named)
    reported["unaccepted_outages"] = unaccepted
    reported["stale_acceptances"] = stale

    if stale:
        logger.warning(
            "sos_raw_harvest_acceptance_stale",
            extra={
                "sources": stale,
                "issues": sorted(o.issue for o in accepted if o.source in stale),
                "detail": "the source recovered — remove the acceptance from ACCEPTED_OUTAGES",
            },
        )
    if reported["accepted_outages"]:
        logger.warning(
            "sos_raw_harvest_accepted_outage",
            extra={
                "sources": reported["accepted_outages"],
                "issues": sorted(
                    o.issue for o in accepted if o.source in reported["accepted_outages"]
                ),
                "observed": sorted(
                    o.observed for o in accepted if o.source in reported["accepted_outages"]
                ),
            },
        )
    if stale or unaccepted:
        return JobResult.degraded(reported)
    return JobResult.ok(reported)


async def _harvest_job(ctx: JobContext) -> JobResult:
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    return job_outcome(await harvest_raw(root, ttl_days=ctx.args.ttl_days))


def main(argv: list[str] | None = None) -> int:
    """Harvest SOS filings + results wires into the raw store."""
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_sos.raw_harvest",
        description="Fetch the biennium's SOS filings + results into the raw file store (#304).",
        extra_args=_add_args,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
