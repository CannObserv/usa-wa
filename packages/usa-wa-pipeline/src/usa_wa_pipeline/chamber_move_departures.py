"""Chamber-move departures probe (#518): a ``departed`` dated at a move to the other chamber.

    python -m usa_wa_pipeline.chamber_move_departures [--db PATH]

``departed`` is person-scoped: it closes every open span the member holds. Dated
at a move into the other chamber, it ends the new tenure on the day it began. A
move is a seat-scoped ``vacated`` on the old seat, plus a ``seated`` on the new
one where needed (ONTOLOGY.md § 3). The roster backfill wrote one,
``margarethurley:1953 departed 1979-11-28``, and only a manual sweep in #514
caught it. The #363 overlay guard masks the shape only when a ``seated`` opens
the new span on the same date, so this probe checks the event's shape, not its
effect.

**The check.** A current ``departed`` is a hit when the member has evidence in
**both** chambers covering its date:

- a roster row covers its term, ``[year, year + TERM_YEARS)`` (the projector's
  arithmetic). A mid-term successor's row is keyed to its group year, so it
  covers from before the seating, which is what makes the two shipped
  acceptances false positives;
- a staged sponsor row covers its biennium.

Each window runs through the January 31 after its last year. The legislature is
sworn in mid-January, so a move at a term boundary recorded as a ``departed`` on
Jan 14 would otherwise fall outside the outgoing term and be missed, while
closing the new span two weeks after its Jan 1 floor.

**Identity.** A roster row carries a printed name, so it is keyed as the
registry keys it, ``<fold>:<first-session-year>`` with the resolver's aliases
and splits applied. Roster and WSL keys then meet at the person registry's live
entity (tombstones followed). A key the registry does not bind stands as its own
identity. A departure no evidence reaches is counted and sampled as
``unmapped``, never passed silently.

Write-free. Reads the built duckdb's two staging tables and, from Postgres, the
current operator events and the person crosswalk. It runs last in the nightly's
probe loop, after publish, so it alerts and never blocks a publish.

Exit ``0`` clean · ``1`` an unaccepted hit or a stale acceptance, each named ·
``4`` degraded: the build holds no roster or sponsor rows, or there are no
current departures, any of which would read as "no hits" having compared nothing.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import KIND_PERSON
from clearinghouse_domain_legislative.operator_events import KIND_DEPARTED
from clearinghouse_domain_legislative.terms import parse_biennium
from usa_wa_adapter_legislature.roster_pdf.audit import TERM_YEARS
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_ALIASES,
    IDENTITY_SPLITS,
    identity_fold,
)
from usa_wa_pipeline.conformed.spans import ROSTER_SOURCE, SOURCE, entity_index
from usa_wa_pipeline.operator_read import EventRow, operator_event_rows
from usa_wa_pipeline.registry_read import crosswalk_rows

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "chamber-move-departures"

_DEFAULT_DB = "data/pipeline.duckdb"

#: ``staging`` field → the staging model it is read from.
STAGING_INPUTS = {"roster": "stg_roster_members", "sponsors": "stg_wsl_sponsors"}

#: How many unmapped departures a report names.
_SAMPLE = 20


@dataclass(frozen=True)
class Acceptance:
    """A hit judged legitimate: the member did leave the legislature on the date."""

    member_id: str
    effective_date: date
    reason: str
    evidence: str

    @property
    def label(self) -> str:
        return f"{self.member_id} {self.effective_date.isoformat()}"


_ROSTER_PDF = "https://leg.wa.gov/media/s4gf4suc/members-of-the-legislature-1889-2025.pdf"

#: The check's two false positives on the live events, judged in #514 and measured
#: again 2026-10-19 over all 304 current departures. Each is a real departure that a
#: mid-term row's window covers. Remove an entry when its event is retracted or
#: superseded; the probe fails on a stale one until then.
ACCEPTED: tuple[Acceptance, ...] = (
    Acceptance(
        "charleslklinefelter:1941",
        date(1943, 11, 22),
        "resigned the Senate (Port Orchard Housing Authority); he resigned the House seat "
        "his 1943 row lists on Dec. 4, 1942, before assuming office",
        f"{_ROSTER_PDF}#page=81",
    ),
    Acceptance(
        "elmerchuntley:1957",
        date(1965, 3, 26),
        "resigned the House (Highway Commission); the Senate row keyed 1965 is his "
        "appointment of April 24, 1967, a real return after a gap",
        f"{_ROSTER_PDF}#page=43",
    ),
)


@dataclass(frozen=True)
class Window:
    """One piece of evidence that the member held a seat in ``chamber``."""

    chamber: str
    start: date
    #: Exclusive: the February 1 after the window's last year.
    end: date
    label: str

    def covers(self, when: date) -> bool:
        return self.start <= when < self.end


@dataclass(frozen=True)
class Hit:
    """A ``departed`` with both chambers' evidence covering its date."""

    member_id: str
    effective_date: date
    chambers: tuple[str, ...]
    evidence: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.member_id} {self.effective_date.isoformat()}"

    def as_counter(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "effective_date": self.effective_date.isoformat(),
            "chambers": list(self.chambers),
            "evidence": list(self.evidence),
        }


def _window_end(last_year: int) -> date:
    """Exclusive end: the window runs through January 31 after ``last_year``."""
    return date(last_year + 1, 2, 1)


def _fold(name: str) -> str:
    fold = identity_fold(name)
    return IDENTITY_ALIASES.get(fold, fold)


def roster_keys(rows: list[dict[str, Any]]) -> list[str | None]:
    """Each roster row's person key, aligned with ``rows`` (``None`` when nameless).

    The registry's key, ``<namespace>:<fold>:<first-session-year>``: aliased folds
    group as one person, and an adjudicated split keys each part by its own
    earliest year, as ``identity.resolve_identities`` mints them.
    """
    groups: dict[tuple[str, bool | None], list[int]] = defaultdict(list)
    parts: list[tuple[str, bool | None] | None] = []
    for row in rows:
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            parts.append(None)
            continue
        fold = _fold(name)
        boundary = IDENTITY_SPLITS.get(fold)
        part = (fold, None if boundary is None else int(row["year"]) >= boundary)
        groups[part].append(int(row["year"]))
        parts.append(part)
    return [
        None if part is None else f"{ROSTER_SOURCE}:{part[0]}:{min(groups[part])}" for part in parts
    ]


def evidence_windows(
    roster: list[dict[str, Any]], sponsors: list[dict[str, Any]]
) -> dict[str, list[Window]]:
    """Natural person key → the chamber windows its rows evidence."""
    windows: dict[str, list[Window]] = defaultdict(list)
    for row, key in zip(roster, roster_keys(roster), strict=True):
        chamber = str(row.get("chamber") or "").lower()
        if key is None or chamber not in TERM_YEARS:
            continue
        year = int(row["year"])
        windows[key].append(
            Window(
                chamber,
                date(year, 1, 1),
                _window_end(year + TERM_YEARS[chamber] - 1),
                f"roster {chamber} {year}",
            )
        )
    for row in sponsors:
        chamber = str(row.get("agency") or "").lower()
        member_id = row.get("member_id")
        if chamber not in TERM_YEARS or not member_id:
            continue
        start, end = parse_biennium(str(row["biennium"]))
        windows[f"{SOURCE}:{member_id}"].append(
            Window(
                chamber,
                date(start, 1, 1),
                _window_end(end),
                f"sponsors {chamber} {row['biennium']}",
            )
        )
    return windows


def _member_key(member_id: str) -> str:
    """An event's member id → its natural key. Roster identities are ``<fold>:<year>``;
    WSL ids are bare numbers (the two disjoint spaces the span families key on)."""
    return f"{ROSTER_SOURCE if ':' in member_id else SOURCE}:{member_id}"


def find_hits(
    events: Iterable[EventRow],
    windows_by_key: dict[str, list[Window]],
    entity_by_key: dict[str, str],
) -> tuple[list[Hit], list[str]]:
    """``(hits, unmapped member ids)`` over the ``departed`` events. Pure."""
    by_identity: dict[str, list[Window]] = defaultdict(list)
    for key, windows in windows_by_key.items():
        by_identity[entity_by_key.get(key, key)].extend(windows)

    hits: list[Hit] = []
    unmapped: list[str] = []
    for event in events:
        if event.kind != KIND_DEPARTED:
            continue
        key = _member_key(event.member_id)
        windows = by_identity.get(entity_by_key.get(key, key))
        if not windows:
            unmapped.append(event.member_id)
            continue
        covering = [w for w in windows if w.covers(event.effective_date)]
        chambers = tuple(sorted({w.chamber for w in covering}))
        if len(chambers) > 1:
            hits.append(
                Hit(
                    event.member_id,
                    event.effective_date,
                    chambers,
                    tuple(sorted({w.label for w in covering})),
                )
            )
    return hits, unmapped


def run_probe(
    staging: dict[str, list[dict[str, Any]]],
    *,
    events: list[EventRow],
    entity_by_key: dict[str, str],
    accepted: Iterable[Acceptance] = ACCEPTED,
) -> JobResult:
    """Find the hits and gate them against ``accepted``. Pure: the job does the reads."""
    departures = [e for e in events if e.kind == KIND_DEPARTED]
    for empty, reason in (
        (not staging["roster"], "empty_roster_rows"),
        (not staging["sponsors"], "empty_sponsors"),
        (not departures, "no_departures"),
    ):
        if empty:
            logger.warning(f"chamber_move_departures_{reason}")
            return JobResult.degraded({reason: True})

    windows = evidence_windows(staging["roster"], staging["sponsors"])
    hits, unmapped = find_hits(departures, windows, entity_by_key)
    accepted_by_label = {a.label: a for a in accepted}
    hit_labels = {h.label for h in hits}
    counters: dict[str, Any] = {
        "departures": len(departures),
        "unmapped": len(unmapped),
        "unmapped_sample": sorted(unmapped)[:_SAMPLE],
        "hits": len(hits),
        "accepted": sorted(h.label for h in hits if h.label in accepted_by_label),
        "unaccepted_hits": [h.as_counter() for h in hits if h.label not in accepted_by_label],
        "stale_acceptances": sorted(set(accepted_by_label) - hit_labels),
    }
    counters["failures"] = [
        name for name in ("unaccepted_hits", "stale_acceptances") if counters[name]
    ]
    if counters["failures"]:
        logger.error("chamber_move_departures_report", extra={"summary": counters})
        return JobResult.failed(counters, exit_code=1)
    logger.info("chamber_move_departures_report", extra={"summary": counters})
    return JobResult.ok(counters)


def load_staging(db_path: str) -> dict[str, list[dict[str, Any]]]:
    """The two staging tables, read back from the built duckdb."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        return {
            field: con.table(model).df().to_dict("records")
            for field, model in STAGING_INPUTS.items()
        }
    finally:
        con.close()


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="Built pipeline duckdb (default: USA_WA_PIPELINE_DB, else data/pipeline.duckdb).",
    )


async def _probe_job(ctx: JobContext) -> JobResult:
    """Read the build, the events and the person registry, then gate."""
    session = ctx.require_session()
    db_path = ctx.args.db or os.environ.get("USA_WA_PIPELINE_DB", _DEFAULT_DB)
    return run_probe(
        load_staging(db_path),
        events=await operator_event_rows(session),
        entity_by_key=entity_index(await crosswalk_rows(session, KIND_PERSON)),
    )


def main(argv: list[str] | None = None) -> int:
    """Flag a member-wide ``departed`` dated at a move to the other chamber."""
    return run_job(
        JOB_SLUG,
        _probe_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.chamber_move_departures",
        description="Write-free: flag a departed event dated at a move to the other chamber.",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
