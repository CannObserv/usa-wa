"""Phase A committee-membership harvester (#82) — archive historical committee rosters.

For each biennium in a range, enumerate that biennium's House/Senate standing committees
**from the local roster archive** (``committees-roster:<biennium>``, written by the
sub-project-3 committee harvest — no extra ``GetCommittees`` call) and fan
``CommitteeService.GetCommitteeMembers(biennium, agency, Name)`` over them through the
:class:`AdapterRunner` under the ``committee-members-hist:<biennium>:<id>:<agency>:<name>``
resource id — archiving each pristine SOAP wire (RawPayload, hashed, #54).

**Persons only.** The runner materializes Person + ``wa_legislature_member_id`` identifier
(idempotently — they already exist from the #77 sponsor harvest, and ``fill_only`` never
clobbers). Committee *membership* is not a per-biennium row: it is a merged span built from
this archive in Phase B (:mod:`harvest_committee_member_spans`).

**Scope.** House/Senate standing committees only. The Joint/``Other`` class (``org_type=
'other'``, meeting-derived, #39) has no membership op at all, consistent with that issue.
Floor is ~1999-00: below it WSL's truncated old committee names don't resolve and the op
faults — swallowed to an empty roster by the transport, so the sweep passes through cleanly.

Pacing is **central**: ``--pause-seconds`` sets the global WSL request limiter (#77), so
every ``GetCommitteeMembers`` POST drips against WSL. Roughly 40 committees × 14 biennia ≈
560 paced calls. Closed rosters are cache hits on re-run.

    python -m usa_wa_adapter_legislature.harvest_committee_members \\
        --from-biennium 1999-00 --pause-seconds 1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.runner import AdapterRunner
from usa_wa_adapter_legislature.adapter import (
    WALegislatureAdapter,
    committee_members_hist_resource_id,
)
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.committee_roster_cohort import CommitteeRosterCohortProvider
from usa_wa_adapter_legislature.harvest_committee_meetings import bienniums_in_range
from usa_wa_adapter_legislature.provisioning import get_or_create_source, resolve_jurisdiction
from usa_wa_adapter_legislature.synthesis import biennium_for_date
from usa_wa_adapter_legislature.transport import WSLClient, configure_wsl_rate_limit

logger = get_logger(__name__)

#: WSL's ``GetCommitteeMembers`` floor — below this, truncated old committee names fault.
DEFAULT_MEMBERSHIP_FLOOR = "1999-00"

#: Only chamber standing committees have a membership op (Joint/`Other` are meeting-derived).
_MEMBER_AGENCIES = ("House", "Senate")


@dataclass(frozen=True)
class HarvestSummary:
    """Counts from one Phase A sweep."""

    bienniums: int
    rosters_pulled: int
    upserted: int
    dry_run: bool


def standing_committees(records: list[dict]) -> list[tuple[str, str, str]]:
    """``[(committee_source_id, agency, short Name)]`` for the chamber standing committees in
    a roster. ``GetCommitteeMembers`` keys on the short ``Name`` — ``LongName`` faults."""
    out: list[tuple[str, str, str]] = []
    for rec in records:
        agency = rec.get("Agency")
        name = (rec.get("Name") or "").strip()
        source_id = rec.get("Id")
        if agency not in _MEMBER_AGENCIES or not name or source_id is None:
            continue
        out.append((str(source_id), agency, name))
    return out


async def harvest_committee_members(
    session: AsyncSession,
    *,
    bienniums: list[str],
    committee_client: WSLClient | None = None,
    member_client: WSLClient | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> HarvestSummary:
    """Archive each (biennium, committee) roster; materialize Persons fill-only.

    Operates in the caller's transaction (the CLI commits, or rolls back on ``dry_run``)."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=bienniums[0], jurisdiction_id=jurisdiction.id
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=jurisdiction.id,
        biennium=bienniums[0],
        client=committee_client,
        member_client=member_client,
        session=session,
    )
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,  # additive; never clobber an existing (PM-curated) Person
    )
    roster_provider = CommitteeRosterCohortProvider(
        committee_client or WSLClient("CommitteeService"), session=session, source_id=source.id
    )

    rosters_pulled = upserted = 0
    for biennium in bienniums:
        committees = standing_committees(await roster_provider.roster_records(biennium))
        if not committees:
            logger.warning("committee_member_harvest_no_committees", extra={"biennium": biennium})
            continue
        for committee_source_id, agency, name in committees:
            resource_id = committee_members_hist_resource_id(
                biennium, committee_source_id, agency, name
            )
            upserted += await runner.fetch_and_normalize(resource_id, force=force)
            rosters_pulled += 1
        logger.info(
            "committee_member_rosters_harvested",
            extra={"biennium": biennium, "committees": len(committees)},
        )

    return HarvestSummary(
        bienniums=len(bienniums),
        rosters_pulled=rosters_pulled,
        upserted=upserted,
        dry_run=dry_run,
    )


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Harvest historical committee rosters (Persons only, #82 Phase A)."
    )
    parser.add_argument(
        "--from-biennium",
        default=DEFAULT_MEMBERSHIP_FLOOR,
        help=f"e.g. 1999-00 (default {DEFAULT_MEMBERSHIP_FLOOR}, the GetCommitteeMembers floor)",
    )
    parser.add_argument("--to-biennium", default=None, help="default: the current biennium")
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=None,
        help="min seconds between WSL calls (sets the central rate limiter)",
    )
    parser.add_argument("--dry-run", action="store_true", help="harvest but roll back")
    parser.add_argument(
        "--force", action="store_true", help="re-fetch past the runner's freshness cache"
    )
    args = parser.parse_args(argv)

    if args.pause_seconds is not None:
        configure_wsl_rate_limit(args.pause_seconds)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2

    to_biennium = args.to_biennium or biennium_for_date(datetime.now(UTC).date())
    bienniums = bienniums_in_range(args.from_biennium, to_biennium)

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            summary = await harvest_committee_members(
                session, bienniums=bienniums, dry_run=args.dry_run, force=args.force
            )
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("committee_member_harvest_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Committee member harvest: bienniums={summary.bienniums} "
        f"rosters={summary.rosters_pulled} upserted={summary.upserted} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
