"""PDC raw-tier harvest (#304): winner-cohort wires into the file store.

    python -m usa_wa_adapter_pdc.raw_harvest [--root PATH] [--ttl-days N]

The file-store sibling of :mod:`usa_wa_adapter_pdc.archive_refresh`, feeding
the #302 pipeline: the same winner cohorts (both House generals + the three
Senate cohorts a biennium's membership is decided by, #121), fetched through
the same :class:`~usa_wa_adapter_pdc.transport.PDCClient`, written as pristine
wires to ``raw/usa_wa_pdc/`` under the same resource ids the Postgres archive
uses — one vocabulary across both stores for #306's staging models. Runs in
parallel with the old pipeline until #302 cutover; per-cohort failures are
contained as ``err`` manifest entries (the SAVEPOINT analog). ``--ttl-days``
skips resources with a fresh ``latest.json`` entry; the default 0 forces the
daily wire, as the archive refresh does.
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
from usa_wa_adapter_pdc.harvest import (
    HOUSE_WINNERS_RESOURCE_PREFIX,
    SENATE_WINNERS_RESOURCE_PREFIX,
)
from usa_wa_adapter_pdc.transport import PDCClient
from usa_wa_common.elections import election_years_for_biennium, senate_election_years_for_biennium

logger = get_logger(__name__)

#: Stable ledger identity (#178); distinct from ``pdc-archive-refresh`` (Postgres tier).
JOB_SLUG = "pdc-raw-harvest"

SOURCE_SLUG = "usa_wa_pdc"


async def harvest_raw(
    root: Path | str,
    *,
    biennium: str | None = None,
    pdc_client: Any | None = None,
    ttl_days: float = 0.0,
) -> dict[str, int]:
    """Fetch the biennium's winner cohorts into the raw store. Returns counters."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    client = pdc_client or PDCClient()
    store = RawStore(root, SOURCE_SLUG)
    run = store.open_run()
    counters = {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 0}

    plan: list[tuple[str, str, Any]] = [
        (f"{HOUSE_WINNERS_RESOURCE_PREFIX}{y}", "house", y)
        for y in election_years_for_biennium(biennium)
    ] + [
        (f"{SENATE_WINNERS_RESOURCE_PREFIX}{y}", "senate", y)
        for y in senate_election_years_for_biennium(biennium)
    ]
    # from the client actually fetching when it can say (CR 45): a mirror or
    # test host must not record a URL that was never requested
    url_source = client if hasattr(client, "winners_url") else PDCClient()
    winners_url = url_source.winners_url()
    try:
        for resource_id, chamber, year in plan:
            params = (
                PDCClient.house_winners_params(year)
                if chamber == "house"
                else PDCClient.senate_winners_params(year)
            )
            await record_fetch(
                run,
                store,
                resource_id,
                # the real, replayable request (#54 provenance) — not a pseudo-URL
                f"{winners_url}?{urlencode(params)}",
                lambda c=chamber, y=year: (
                    client.fetch_house_winners(y)
                    if c == "house"
                    else client.fetch_senate_winners(y)
                ),
                counters,
                ttl_days,
                log_event="pdc_raw_harvest_cohort_failed",
            )
    finally:
        run.close()
    logger.info("pdc_raw_harvest_complete", extra={"biennium": biennium, **counters})
    return counters


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")
    parser.add_argument(
        "--ttl-days",
        type=float,
        default=0.0,
        help="Skip resources fetched ok within N days (0 = always fetch, the daily default).",
    )


def job_outcome(counters: dict[str, int]) -> JobResult:
    """Degraded when the source landed nothing, or when every attempted fetch
    failed — a whole-source outage must alert even when TTL skips mask it."""
    landed_nothing = counters["fetched"] == 0 and counters["skipped_fresh"] == 0
    every_attempt_failed = counters["errors"] > 0 and counters["fetched"] == 0
    if landed_nothing or every_attempt_failed:
        return JobResult.degraded(counters)
    return JobResult.ok(counters)


async def _harvest_job(ctx: JobContext) -> JobResult:
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    return job_outcome(await harvest_raw(root, ttl_days=ctx.args.ttl_days))


def main(argv: list[str] | None = None) -> int:
    """Harvest PDC winner wires into the raw store. Exit ``0`` ok · ``4`` whole-source outage."""
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_pdc.raw_harvest",
        description="Fetch the biennium's PDC winner cohorts into the raw file store (#304).",
        extra_args=_add_args,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
