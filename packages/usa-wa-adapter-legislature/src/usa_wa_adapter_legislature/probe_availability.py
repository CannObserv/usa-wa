"""Write-free availability probe for a biennium's WSL rosters (#135 item 4).

    python -m usa_wa_adapter_legislature.probe_availability --biennium 2027-28 \\
        --log data/research/wsl-availability.jsonl --until 2027-02-28

Measured, not assumed: the rollover's nightly warnings last exactly as long as WSL takes to
publish the new biennium — ``assignments_chamber_vacancy`` until ``GetSponsors`` has rows, the
raw harvest's degraded run until ``GetCommittees`` stops faulting (the 2026-10-01 rehearsal:
``GetSponsors(2027-28)`` answers empty, ``GetCommittees(2027-28)`` raises a server fault). So
this asks the three roster operations about one biennium and appends the counts to a JSONL
log, one line per run, which ``docs/research/`` turns into dates.

**A change is the news** — the roster re-check's convention (#237): a run whose counts differ
from the previous measurement of the same biennium exits ``4`` (``EXIT_DEGRADED``), so the
``OnFailure=`` mail says "WSL started publishing" the morning it does; an unchanged run exits
``0``. A first measurement that finds nothing is a baseline, not news. The fault text is
recorded but never compared: a reworded server error is not a measurement. An outage that is
not a ``GetCommittees`` fault — a transport error, a ``GetSponsors`` fault — fails the run.

Write-free: it touches neither the raw store nor the database (``needs_db=False``, no ledger
row), so it can run beside the nightly without moving anything the pipeline reads.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from zeep.exceptions import Fault

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)

#: Stable ledger identity (#178) — named in the summary line; no ledger row is written.
JOB_SLUG = "wsl-availability-probe"

#: The fields a measurement is compared on. ``committee_fault`` is evidence, not a count.
MEASURED = (
    "sponsors_house",
    "sponsors_senate",
    "committees",
    "committees_with_members",
    "committee_members",
)


@dataclass(frozen=True)
class Availability:
    """One biennium's WSL rosters as they answered at one moment.

    ``committees`` is ``None`` when ``GetCommittees`` faulted — unpublished, as distinct
    from a published biennium with no committees (``0``)."""

    biennium: str
    sponsors_house: int
    sponsors_senate: int
    committees: int | None
    committees_with_members: int
    committee_members: int
    committee_fault: str | None

    def as_record(self, checked_at: datetime) -> dict[str, Any]:
        """The JSONL line: the measurement plus when it was taken (ISO 8601 UTC, ``Z``)."""
        stamp = checked_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return {"checked_at": stamp, **asdict(self)}


async def measure(
    biennium: str, *, sponsor_client: Any | None = None, committee_client: Any | None = None
) -> Availability:
    """Ask ``GetSponsors``, ``GetCommittees`` and each committee's ``GetCommitteeMembers``."""
    sponsors = sponsor_client or WSLClient("SponsorService")
    committees_client = committee_client or WSLClient("CommitteeService")

    members = (await sponsors.fetch_sponsors(biennium)).records
    agencies = [row.get("Agency") for row in members]

    try:
        committees = (await committees_client.fetch_committees(biennium)).records
        fault = None
    except Fault as exc:
        committees, fault = None, str(exc)

    with_members = total_members = 0
    for committee in committees or []:
        roster = await committees_client.fetch_historical_committee_members(
            biennium, committee.get("Agency"), committee.get("Name")
        )
        if roster.records:
            with_members += 1
            total_members += len(roster.records)

    return Availability(
        biennium=biennium,
        sponsors_house=agencies.count("House"),
        sponsors_senate=agencies.count("Senate"),
        committees=None if committees is None else len(committees),
        committees_with_members=with_members,
        committee_members=total_members,
        committee_fault=fault,
    )


def changed_fields(previous: dict[str, Any] | None, current: Availability) -> list[str]:
    """The measured fields that moved since ``previous`` (a logged record of the same
    biennium). With no previous record, the non-empty ones: a first look that finds rows is
    news, a first look that finds nothing is the baseline."""
    now = asdict(current)
    if previous is None:
        return [name for name in MEASURED if now[name]]
    return [name for name in MEASURED if previous.get(name) != now[name]]


def _previous(log: Path, biennium: str) -> dict[str, Any] | None:
    if not log.exists():
        return None
    latest = None
    for line in log.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            if record.get("biennium") == biennium:
                latest = record
    return latest


async def probe(
    biennium: str,
    log: Path,
    *,
    until: date | None = None,
    today: date | None = None,
    record: bool = True,
    sponsor_client: Any | None = None,
    committee_client: Any | None = None,
) -> JobResult:
    """Measure, append to ``log``, and report a change as degraded (the alert, #237).

    Past ``until`` it asks nothing and logs nothing: the question is the rollover's, and
    once it is answered a mid-biennium committee reshuffle would only keep mailing "news".
    ``record=False`` (``--dry-run``) measures and compares but appends nothing."""
    if until is not None and (today or datetime.now(UTC).date()) > until:
        return JobResult.ok({"window_closed": until.isoformat()})
    current = await measure(
        biennium, sponsor_client=sponsor_client, committee_client=committee_client
    )
    changed = changed_fields(_previous(log, biennium), current)
    if record:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a") as handle:
            handle.write(json.dumps(current.as_record(datetime.now(UTC))) + "\n")

    counters = {**asdict(current), "changed": changed}
    if changed:
        logger.warning("wsl_availability_changed", extra={"biennium": biennium, "changed": changed})
        return JobResult.degraded(counters)
    return JobResult.ok(counters)


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--biennium", required=True, help="The biennium to ask about (2027-28).")
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("data/research/wsl-availability.jsonl"),
        help="JSONL measurement log, appended per run (default data/research/…).",
    )
    parser.add_argument(
        "--until",
        type=date.fromisoformat,
        default=None,
        help="Last day to measure (YYYY-MM-DD); later runs ask nothing and exit 0.",
    )


async def _probe_job(ctx: JobContext) -> JobResult:
    return await probe(
        ctx.args.biennium, ctx.args.log, until=ctx.args.until, record=not ctx.dry_run
    )


def main(argv: list[str] | None = None) -> int:
    """Run the probe: exit 0 unchanged, 4 changed (news), 1 outage."""
    return run_job(
        JOB_SLUG,
        _probe_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.probe_availability",
        description="Write-free probe: has WSL published a biennium's rosters yet? (#135)",
        extra_args=_add_args,
        needs_db=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
