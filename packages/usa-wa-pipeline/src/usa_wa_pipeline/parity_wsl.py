"""WSL staging-parity probe (#306): staging outputs vs. canonical Postgres.

    python -m usa_wa_pipeline.parity_wsl [--root PATH] [--json]

Write-free probe, the transition oracle's first comparator (replatform spec
§ Transition plan): re-derives the legislature staging rows from the raw store
with the REAL offline parsers and diffs their key sets against the live
canonical tables the old pipeline maintains.

- **committees** — staging `committees-roster:*` ids ∪ the **Joint/`Other`**
  meeting committee refs (the only agencies the canonical meeting path ingests,
  #39 — House/Senate refs ride along in meeting data but are
  `CommitteeService`'s domain) vs. `Organization(source='usa_wa_legislature',
  org_type='committee')` source ids, archived + dissolved included (history is
  the point).
- **sponsors** — staging member ids from the sponsor wires ∪ the
  committee-member wires (both member resources mint the Person cluster, P1b)
  vs. `Person(source='usa_wa_legislature')` source ids.

Roster identity parity is deliberately absent: roster staging rows are
pre-resolution member-years, while canonical roster Persons are the OUTPUT of
succession→resolve; that comparison belongs to the matching tier's parity
(#308), where the resolution is rebuilt.

Exit ``0`` clean · ``1`` divergent (each report rendered to the log). Accepted
divergences live in :data:`ACCEPTED` with a named reason each; a stale
acceptance fails the run (a blindfold nobody revisits, the #300 rule).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import RawStore, get_raw_root
from clearinghouse_domain_legislative.identity import Organization, Person
from usa_wa_pipeline.parity import AcceptedDiff, ParityReport, key_set_parity
from usa_wa_pipeline.staging import wsl

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "parity-wsl-staging"

SOURCE = "usa_wa_legislature"

#: The agencies whose meeting refs the canonical path ingests (#39) — mirror of
#: `normalize/committee_meetings._MEETING_DERIVED_AGENCIES`.
MEETING_DERIVED_AGENCIES = frozenset({"Joint", "Other"})

#: Joint/`Other` bodies present in ARCHIVED meeting windows that canonical never
#: materialized: the #39 ingestion discovers from the current window daily, and
#: the historical windows were archived Phase-A-only. Staging reads the whole
#: archive, so it sees them — canonical ⊆ staging, nothing lost; these become
#: orgs properly in the conformed tier. Verified by name against the wires
#: (2026-09-03): all twenty are real bodies (JLARC, Joint Transportation, …).
_ARCHIVED_MEETING_BODIES = frozenset(
    {
        "-141",
        "-140",
        "-74",
        "-71",
        "-27",
        "-14",
        "-12",
        "-5",
        "-4",
        "13945",
        "15660",
        "17159",
        "18046",
        "18048",
        "18059",
        "21488",
        "22601",
        "27992",
        "28113",
        "34724",
        # 35341 (Joint Select Cmte on Civic Health, 2023-24 window) and 36496
        # (Joint Leg-Exec Cmte on Budget Transparency, 2025-26 window) — same
        # class; 36496 being CURRENT-window suggests the #39 daily discovery
        # may have skipped it (flagged on the issue for follow-up).
        "35341",
        "36496",
    }
)

#: Explained-and-accepted divergences. Empty is the goal state; every entry
#: names its reason and dies loudly once the divergence heals.
ACCEPTED: tuple[AcceptedDiff, ...] = tuple(
    AcceptedDiff(
        key,
        "staging",
        "Joint/Other body from an archived meeting window canonical never "
        "normalized (#39 is current-window discovery; historical windows were "
        "Phase-A-only)",
    )
    for key in sorted(_ARCHIVED_MEETING_BODIES)
) + (
    AcceptedDiff(
        "31656",
        "canonical",
        "Denny Heck (Lt. Governor): ex-officio Senate Rules seat minted from the "
        "retired `committee-members:` archive (GetActiveCommitteeMembers); the "
        "live `committee-members-hist:` vocabulary excludes non-legislator "
        "ex-officio members. The retired archive's treatment is a conformed-tier "
        "decision (#309).",
    ),
)


async def run_parity(
    session: AsyncSession,
    store: RawStore,
    *,
    committee_rows: Callable[[RawStore], list[dict[str, Any]]] = wsl.committee_rows,
    meeting_rows: Callable[[RawStore], list[dict[str, Any]]] = wsl.meeting_rows,
    sponsor_rows: Callable[[RawStore], list[dict[str, Any]]] = wsl.sponsor_rows,
    committee_member_rows: Callable[[RawStore], list[dict[str, Any]]] = wsl.committee_member_rows,
) -> list[ParityReport]:
    """Build both reports. Row-builders injectable for tests; read-only on the DB."""
    staging_committees = {r["committee_id"] for r in committee_rows(store)} | {
        r["committee_id"]
        for r in meeting_rows(store)
        if r["committee_agency"] in MEETING_DERIVED_AGENCIES
    }
    canonical_committees = set(
        (
            await session.execute(
                select(Organization.source_id).where(
                    Organization.source == SOURCE, Organization.org_type == "committee"
                )
            )
        ).scalars()
    )
    staging_sponsors = {r["member_id"] for r in sponsor_rows(store)} | {
        r["member_id"] for r in committee_member_rows(store)
    }
    canonical_sponsors = set(
        (await session.execute(select(Person.source_id).where(Person.source == SOURCE))).scalars()
    )
    accepted = [d for d in ACCEPTED]
    return [
        key_set_parity(
            "committees",
            staging_committees,
            canonical_committees,
            accepted=[d for d in accepted if d.key in staging_committees | canonical_committees],
        ),
        key_set_parity(
            "sponsors",
            staging_sponsors,
            canonical_sponsors,
            accepted=[d for d in accepted if d.key in staging_sponsors | canonical_sponsors],
        ),
    ]


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")


async def _parity_job(ctx: JobContext) -> JobResult:
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    store = RawStore(root, SOURCE)
    if not store.latest():
        logger.warning("parity_wsl_empty_store", extra={"root": str(root)})
        return JobResult.degraded({"empty_store": True})
    reports = await run_parity(ctx.require_session(), store)
    counters = {
        r.dataset: {
            "staging": r.staging_total,
            "canonical": r.canonical_total,
            "only_staging": len(r.only_staging),
            "only_canonical": len(r.only_canonical),
            "accepted": len(r.accepted),
        }
        for r in reports
    }
    for report in reports:
        log = logger.info if report.clean else logger.error
        log("parity_wsl_report", extra={"report": report.render()})
    if all(r.clean for r in reports):
        return JobResult.ok(counters)
    return JobResult.failed(counters, exit_code=1)


def main(argv: list[str] | None = None) -> int:
    """Diff WSL staging vs. canonical. Exit ``0`` clean · ``1`` divergent · ``4`` no store."""
    return run_job(
        JOB_SLUG,
        _parity_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.parity_wsl",
        description="Write-free parity probe: WSL staging rows vs. canonical Postgres (#306).",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
