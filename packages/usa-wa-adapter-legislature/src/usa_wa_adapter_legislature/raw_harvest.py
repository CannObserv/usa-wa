"""WSL raw-tier harvest (#304): the daily SOAP set + member fan-out into files.

    python -m usa_wa_adapter_legislature.raw_harvest [--root PATH] [--ttl-days N]

The file-store sibling of :mod:`usa_wa_adapter_legislature.refresh`'s Phase-A
pulls, feeding the #302 pipeline: the biennium committee roster
(``GetCommittees``), the active-committee snapshot, the full-biennium meeting
window, the sponsor roster, and the per-committee ``GetCommitteeMembers``
fan-out — under the same resource ids the Postgres archive uses. Two deliberate
differences from the daily refresh: the fan-out enumerates committees from the
roster wire fetched *in this run* (parsed offline through the same SOAP
binding), never from Postgres — the raw tier must be buildable with no
database — and nothing here normalizes; bytes in, bytes stored. Every SOAP
call passes through the central WSL rate limiter (#77) inside the transport,
so the sequential fan-out cannot burst the host. Per-resource failures are
contained as ``err`` manifest entries; a dead roster kills only the fan-out.
"""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import RawRun, RawStore, get_raw_root
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.adapter import (
    COMMITTEES_RESOURCE_PREFIX,
    COMMITTEES_ROSTER_RESOURCE_PREFIX,
    SPONSORS_RESOURCE_PREFIX,
    committee_members_hist_resource_id,
)
from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG
from usa_wa_adapter_legislature.meetings.windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.transport import WSL_BASE_URL, WSLClient

logger = get_logger(__name__)

#: Stable ledger identity (#178); distinct from the Postgres-tier ``wsl-refresh``.
JOB_SLUG = "wsl-raw-harvest"

SOURCE_SLUG = WSL_SOURCE_SLUG


async def _record(
    run: RawRun,
    store: RawStore,
    resource_id: str,
    url: str,
    fetcher: Any,
    counters: dict[str, int],
    ttl_days: float,
) -> Any | None:
    """Fetch one resource into the run; returns the fetch (for fan-out) or None."""
    if ttl_days and store.is_fresh(resource_id, ttl_days=ttl_days):
        run.record(resource_id, None, url=url, status="skipped")
        counters["skipped_fresh"] += 1
        return None
    try:
        fetch = await fetcher()
    except Exception:
        logger.exception("wsl_raw_harvest_fetch_failed", extra={"resource_id": resource_id})
        run.record(resource_id, None, url=url, status="err")
        counters["errors"] += 1
        return None
    recorded = run.record(
        resource_id, fetch.wire, url=url, content_type=getattr(fetch, "content_type", None)
    )
    counters["fetched"] += 1
    if not recorded.newly_stored:
        counters["unchanged"] += 1
    return fetch


async def harvest_raw(
    root: Path | str,
    *,
    biennium: str | None = None,
    committee_client: Any | None = None,
    meeting_client: Any | None = None,
    sponsor_client: Any | None = None,
    ttl_days: float = 0.0,
) -> dict[str, int]:
    """Fetch the daily WSL set + member fan-out into the raw store."""
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    committees = committee_client or WSLClient("CommitteeService")
    meetings = meeting_client or WSLClient("CommitteeMeetingService")
    sponsors = sponsor_client or WSLClient("SponsorService")
    store = RawStore(root, SOURCE_SLUG)
    run = store.open_run()
    counters = {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 0}
    service = f"{WSL_BASE_URL}/CommitteeService.asmx"

    roster = await _record(
        run,
        store,
        f"{COMMITTEES_ROSTER_RESOURCE_PREFIX}{biennium}",
        f"{service}?biennium={biennium}#GetCommittees",
        lambda: committees.fetch_committees(biennium),
        counters,
        ttl_days,
    )
    await _record(
        run,
        store,
        f"{COMMITTEES_RESOURCE_PREFIX}{biennium}",
        f"{service}#GetActiveCommittees",
        committees.fetch_active_committees,
        counters,
        ttl_days,
    )
    begin, end = biennium_window(biennium)
    await _record(
        run,
        store,
        meetings_resource_id(begin, end),
        f"{WSL_BASE_URL}/CommitteeMeetingService.asmx#GetCommitteeMeetings",
        lambda: meetings.fetch_committee_meetings(begin, end),
        counters,
        ttl_days,
    )
    await _record(
        run,
        store,
        f"{SPONSORS_RESOURCE_PREFIX}{biennium}",
        f"{WSL_BASE_URL}/SponsorService.asmx?biennium={biennium}#GetSponsors",
        lambda: sponsors.fetch_sponsors(biennium),
        counters,
        ttl_days,
    )

    if roster is not None:
        for committee in await committees.parse_committees(roster.wire):
            committee_id = committee.get("Id")
            agency = committee.get("Agency")
            name = committee.get("Name")
            if committee_id is None or not agency or not name:
                logger.warning("wsl_raw_harvest_committee_unkeyed", extra={"record": committee})
                continue
            await _record(
                run,
                store,
                committee_members_hist_resource_id(biennium, str(committee_id), agency, name),
                f"{service}?biennium={biennium}&committee={committee_id}#GetCommitteeMembers",
                lambda a=agency, n=name: committees.fetch_historical_committee_members(
                    biennium, a, n
                ),
                counters,
                ttl_days,
            )

    run.close()
    logger.info("wsl_raw_harvest_complete", extra={"biennium": biennium, **counters})
    return counters


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")
    parser.add_argument(
        "--ttl-days",
        type=float,
        default=0.0,
        help="Skip resources fetched ok within N days (0 = always fetch, the daily default).",
    )


async def _harvest_job(ctx: JobContext) -> JobResult:
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    counters = await harvest_raw(root, ttl_days=ctx.args.ttl_days)
    if counters["fetched"] == 0 and counters["skipped_fresh"] == 0:
        return JobResult.degraded(counters)
    return JobResult.ok(counters)


def main(argv: list[str] | None = None) -> int:
    """Harvest the daily WSL SOAP set into the raw store."""
    return run_job(
        JOB_SLUG,
        _harvest_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.raw_harvest",
        description="Fetch the daily WSL SOAP set + member fan-out into the raw file store (#304).",
        extra_args=_add_args,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
