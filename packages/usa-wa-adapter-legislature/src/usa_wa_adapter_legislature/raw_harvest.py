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
from clearinghouse_core.rawstore import RawStore, get_raw_root, record_fetch
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
    counters = {
        "fetched": 0,
        "unchanged": 0,
        "skipped_fresh": 0,
        "errors": 0,
        "fanout_skipped": 0,
        "fanout_attempted": 0,
        "fanout_landed": 0,
    }
    service = f"{WSL_BASE_URL}/CommitteeService.asmx"
    roster_resource = f"{COMMITTEES_ROSTER_RESOURCE_PREFIX}{biennium}"

    try:
        roster = await record_fetch(
            run,
            store,
            roster_resource,
            f"{service}?biennium={biennium}#GetCommittees",
            lambda: committees.fetch_committees(biennium),
            counters,
            ttl_days,
            log_event="wsl_raw_harvest_fetch_failed",
        )
        await record_fetch(
            run,
            store,
            f"{COMMITTEES_RESOURCE_PREFIX}{biennium}",
            f"{service}#GetActiveCommittees",
            committees.fetch_active_committees,
            counters,
            ttl_days,
            log_event="wsl_raw_harvest_fetch_failed",
        )
        begin, end = biennium_window(biennium)
        await record_fetch(
            run,
            store,
            meetings_resource_id(begin, end),
            f"{WSL_BASE_URL}/CommitteeMeetingService.asmx#GetCommitteeMeetings",
            lambda: meetings.fetch_committee_meetings(begin, end),
            counters,
            ttl_days,
            log_event="wsl_raw_harvest_fetch_failed",
        )
        await record_fetch(
            run,
            store,
            f"{SPONSORS_RESOURCE_PREFIX}{biennium}",
            f"{WSL_BASE_URL}/SponsorService.asmx?biennium={biennium}#GetSponsors",
            lambda: sponsors.fetch_sponsors(biennium),
            counters,
            ttl_days,
            log_event="wsl_raw_harvest_fetch_failed",
        )

        roster_wire = _roster_wire(store, roster, roster_resource)
        parsed: list[dict] | None = None
        if roster_wire is not None and not roster_wire:
            # The archived form of a benign fault (#82, CR 38): an
            # out-of-coverage biennium returns an EMPTY wire — no committees,
            # not a lost fan-out. The transport's parse raises on zero bytes,
            # so short-circuit exactly as usa_wa_adapter_legislature.parsing does.
            parsed = []
        elif roster_wire is not None:
            try:
                parsed = await committees.parse_committees(roster_wire)
            except Exception:
                # An HTTP-200 roster that does not parse must not abandon the
                # run ledger: contain, skip the fan-out, degrade at job level.
                logger.exception(
                    "wsl_raw_harvest_roster_unparseable", extra={"resource_id": roster_resource}
                )
        if parsed is None:
            counters["fanout_skipped"] = 1
        else:
            for committee in parsed:
                committee_id = committee.get("Id")
                agency = committee.get("Agency")
                name = committee.get("Name")
                if committee_id is None or not agency or not name:
                    logger.warning("wsl_raw_harvest_committee_unkeyed", extra={"record": committee})
                    continue
                counters["fanout_attempted"] += 1
                member = await record_fetch(
                    run,
                    store,
                    committee_members_hist_resource_id(biennium, str(committee_id), agency, name),
                    f"{service}?biennium={biennium}&committee={committee_id}#GetCommitteeMembers",
                    lambda a=agency, n=name: committees.fetch_historical_committee_members(
                        biennium, a, n
                    ),
                    counters,
                    ttl_days,
                    log_event="wsl_raw_harvest_fetch_failed",
                )
                if not member.error:
                    counters["fanout_landed"] += 1
    finally:
        # An uncontained failure (corrupt latest.json, cancellation) must not
        # abandon already-fetched wires as unmanifested strays (#302 CR).
        run.close()
    logger.info("wsl_raw_harvest_complete", extra={"biennium": biennium, **counters})
    return counters


def _roster_wire(store: RawStore, roster: Any, resource_id: str) -> bytes | None:
    """The roster wire the member fan-out enumerates from: this run's fetch, or
    the stored latest wire when the roster was TTL-fresh — a fresh roster must
    not suppress the members' own TTL/retry decisions (#302 CR)."""
    if roster.payload is not None:
        return roster.payload.wire
    if roster.skipped_fresh:
        entry = store.latest().get(resource_id)
        if entry and entry.get("sha256"):
            path = store.object_path(entry["sha256"])
            if path.is_file():
                return path.read_bytes()
    return None


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")
    parser.add_argument(
        "--ttl-days",
        type=float,
        default=0.0,
        help="Skip resources fetched ok within N days (0 = always fetch, the daily default).",
    )


def job_outcome(counters: dict[str, int]) -> JobResult:
    """Degraded when the source landed nothing, when every attempted fetch
    failed (a TTL-masked outage), or when the member fan-out was lost — #49
    alerting must fire for each of these real degradations (#302 CR).

    Fan-out loss has two shapes (CR 38/39): the roster wire unusable
    (``fanout_skipped``, parse-level) and every member fetch erroring
    (``fanout_attempted`` with nothing landed, fetch-level). A benign empty
    roster attempts no fan-out and is NOT a loss."""
    landed_nothing = counters["fetched"] == 0 and counters["skipped_fresh"] == 0
    every_attempt_failed = counters["errors"] > 0 and counters["fetched"] == 0
    fanout_lost = counters.get("fanout_skipped") or (
        counters.get("fanout_attempted", 0) > 0 and counters.get("fanout_landed", 0) == 0
    )
    if landed_nothing or every_attempt_failed or fanout_lost:
        return JobResult.degraded(counters)
    return JobResult.ok(counters)


async def _harvest_job(ctx: JobContext) -> JobResult:
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    return job_outcome(await harvest_raw(root, ttl_days=ctx.args.ttl_days))


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
