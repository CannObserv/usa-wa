"""Write-free probe of how far back WSL committee/meeting data reaches (#64,
sub-project 1).

Answers "how much history exists" — the input to scoping sub-project 3's backfill.
Walks bienniums **backward** from the current one, calling
``CommitteeService.GetCommittees(biennium)`` and
``CommitteeMeetingService.GetCommitteeMeetings(window)`` for each, tallying committee
rows, meeting records, and meeting wire bytes. Stops after ``--max-empty`` consecutive
empty bienniums (a body absent from *both* services → earliest available reached),
bounded by ``--max-bienniums`` so a never-empty source can't loop forever.

**Read-only, no archival.** It talks to :class:`WSLClient` directly — **not** the
:class:`AdapterRunner` — so exploratory pulls never write a ``FetchEvent`` /
``RawPayload``. We learn the extent before committing to archive it; the real backfill
(sub-project 3) does archival properly through the runner.

Committee wire bytes are not measured (``GetCommittees`` returns parsed rows, not the
envelope, and committee payloads are small); the reported wire volume is the meeting
docket, which dominates (~MB per window).

    python -m usa_wa_adapter_legislature.probe_committee_extent
    python -m usa_wa_adapter_legislature.probe_committee_extent --start-biennium 2025-26 --json
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.meeting_windows import biennium_window
from usa_wa_adapter_legislature.refresh import biennium_for_date, previous_biennium
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)

#: Default earliest-boundary heuristic: N consecutive empty bienniums = done.
DEFAULT_MAX_EMPTY = 2
#: Safety cap on the backward walk (WA bienniums reach back to statehood, ~1889;
#: 80 windows dwarfs that) so a never-empty upstream can't spin forever.
DEFAULT_MAX_BIENNIUMS = 80


async def probe_extent(
    committee_client: Any,
    meeting_client: Any,
    *,
    start_biennium: str,
    max_empty: int = DEFAULT_MAX_EMPTY,
    max_bienniums: int = DEFAULT_MAX_BIENNIUMS,
) -> dict:
    """Walk bienniums backward from ``start_biennium``, tallying per-biennium extent.

    A biennium is *empty* when both services return nothing. The walk stops after
    ``max_empty`` consecutive empties (``stopped_after_empty`` records the run that
    tripped it) or when ``max_bienniums`` is hit (``stopped_after_empty`` stays 0 —
    the cap, not the boundary). Returns a JSON-able summary including every probed
    biennium (trailing empties included) and the earliest one that carried data.
    """
    rows: list[dict] = []
    biennium = start_biennium
    consecutive_empty = 0
    earliest_with_data: str | None = None

    for _ in range(max_bienniums):
        committees = await committee_client.get_committees(biennium)
        begin, end = biennium_window(biennium)
        wire_fetch = await meeting_client.fetch_committee_meetings(begin, end)
        c_count = len(committees)
        m_count = len(wire_fetch.records)
        wire_bytes = len(wire_fetch.wire)
        rows.append(
            {
                "biennium": biennium,
                "committee_count": c_count,
                "meeting_count": m_count,
                "meeting_wire_bytes": wire_bytes,
            }
        )
        if c_count == 0 and m_count == 0:
            consecutive_empty += 1
        else:
            consecutive_empty = 0
            earliest_with_data = biennium
        logger.info(
            "probe_biennium",
            extra={
                "biennium": biennium,
                "committees": c_count,
                "meetings": m_count,
                "wire_bytes": wire_bytes,
            },
        )
        if consecutive_empty >= max_empty:
            break
        biennium = previous_biennium(biennium)

    totals = {
        "committee_count": sum(r["committee_count"] for r in rows),
        "meeting_count": sum(r["meeting_count"] for r in rows),
        "meeting_wire_bytes": sum(r["meeting_wire_bytes"] for r in rows),
    }
    return {
        "start_biennium": start_biennium,
        "bienniums": rows,
        "bienniums_probed": len(rows),
        "bienniums_with_data": sum(1 for r in rows if r["committee_count"] or r["meeting_count"]),
        "earliest_with_data": earliest_with_data,
        "stopped_after_empty": consecutive_empty if consecutive_empty >= max_empty else 0,
        "totals": totals,
    }


async def probe_committee_floor(
    committee_client: Any,
    *,
    start_biennium: str,
    max_empty: int = DEFAULT_MAX_EMPTY,
    max_bienniums: int = DEFAULT_MAX_BIENNIUMS,
) -> dict:
    """Committee-only backward walk to the earliest biennium ``GetCommittees`` returns
    data (sub-project 3 Phase A). Unlike :func:`probe_extent` it makes **no meeting
    calls** — ``CommitteeService`` reaches back toward statehood, far deeper than the
    meeting docket, and the slow ~MB meeting pulls would dominate an already-long
    sweep. Stops after ``max_empty`` consecutive empty bienniums (bounded by
    ``max_bienniums``). Returns the floor + per-biennium committee counts to scope the
    harvest range."""
    rows: list[dict] = []
    biennium = start_biennium
    consecutive_empty = 0
    earliest_with_data: str | None = None

    for _ in range(max_bienniums):
        committees = await committee_client.get_committees(biennium)
        c_count = len(committees)
        rows.append({"biennium": biennium, "committee_count": c_count})
        if c_count == 0:
            consecutive_empty += 1
        else:
            consecutive_empty = 0
            earliest_with_data = biennium
        logger.info("probe_committee_floor", extra={"biennium": biennium, "committees": c_count})
        if consecutive_empty >= max_empty:
            break
        biennium = previous_biennium(biennium)

    return {
        "start_biennium": start_biennium,
        "bienniums": rows,
        "bienniums_probed": len(rows),
        "earliest_with_data": earliest_with_data,
        "stopped_after_empty": consecutive_empty if consecutive_empty >= max_empty else 0,
        "totals": {"committee_count": sum(r["committee_count"] for r in rows)},
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_adapter_legislature.probe_committee_extent",
        description="Write-free probe of WSL committee/meeting historical extent.",
    )
    parser.add_argument(
        "--start-biennium", default=None, help="start label (default: current from date)"
    )
    parser.add_argument("--max-empty", type=int, default=DEFAULT_MAX_EMPTY)
    parser.add_argument("--max-bienniums", type=int, default=DEFAULT_MAX_BIENNIUMS)
    parser.add_argument("--json", action="store_true", help="emit the summary as JSON")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    start = args.start_biennium or biennium_for_date(datetime.now(UTC).date())
    committee_client = WSLClient("CommitteeService")
    meeting_client = WSLClient("CommitteeMeetingService")
    return await probe_extent(
        committee_client,
        meeting_client,
        start_biennium=start,
        max_empty=args.max_empty,
        max_bienniums=args.max_bienniums,
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, indent=None if args.json else 2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
