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
    filings_url = SOSFilingsClient().export_url()

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
                SOSResultsClient().export_index_url(results_election_date(year)),
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


def job_outcome(counters: dict[str, Any]) -> JobResult:
    """Degraded when EITHER source landed nothing or lost every attempted fetch
    (#302 CR): per-source outage semantics, matching ``sos-filings-harvest``."""
    if _source_degraded(counters["filings"]) or _source_degraded(counters["results"]):
        return JobResult.degraded(counters)
    return JobResult.ok(counters)


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
