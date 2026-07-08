"""CLI entrypoint for one refresh cycle of the WSL adapter.

Usage:
  python -m usa_wa_adapter_legislature.refresh

Reads ``DATABASE_URL`` from the environment, computes the current biennium
(override with ``USA_WA_BIENNIUM``), resolves the ``usa-wa`` jurisdiction and
gets-or-creates the ``usa_wa_legislature`` Source row (both now in
:mod:`usa_wa_adapter_legislature.provisioning`, shared with the harvests),
bootstraps the synthetic anchors (legislature, chambers, biennium + regular
sessions), and runs one :class:`AdapterRunner.refresh` cycle.

Designed to be invoked from cron or systemd. The committees pull is idempotent
on re-run within the source's cache TTL (no live SOAP call, no new rows). The
meeting-window pull is forced past the TTL (#63) — one SOAP call per run — but
only for the date-current biennium; a pinned/backfill ``USA_WA_BIENNIUM`` names
closed history and stays TTL-governed (logged at ``warning`` — routine daily runs
are always current, so a warning means a manual backfill or a stale env pin).
Archival growth stays bounded either way by the unchanged-hash dedup guard.

Biennium computation: WA bienniums begin on odd years. Even-year dates roll
back to the prior odd year (``2026-06-18`` → ``2025-26``). Override via
``USA_WA_BIENNIUM`` for testing or early-year edge cases.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Citation, FetchEvent
from clearinghouse_core.runner import AdapterRunner, RunSummary
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.adapter import (
    COMMITTEES_RESOURCE_PREFIX,
    SPONSORS_RESOURCE_PREFIX,
    WALegislatureAdapter,
    committee_members_resource_id,
)
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors, bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.meeting_windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.provisioning import get_or_create_source, resolve_jurisdiction
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)


def biennium_for_date(today: date) -> str:
    """Compute the WA biennium label (``YYYY-YY``) covering ``today``.

    Bienniums begin on odd years (2025-26, 2027-28, …). On an even year we
    roll back to the prior odd year.
    """
    start = today.year if today.year % 2 == 1 else today.year - 1
    end_suffix = (start + 1) % 100
    return f"{start}-{end_suffix:02d}"


def _biennium_start_year(label: str) -> int:
    """Parse the odd start year from a ``YYYY-YY`` biennium label."""
    return int(label.split("-", 1)[0])


def biennium_start_date(label: str) -> date:
    """The date a biennium begins — Jan 1 of its odd start year.

    WSL exposes no explicit committee name-change date; this biennium-start boundary
    is the documented approximation used to window a detected rename (#46).
    """
    return date(_biennium_start_year(label), 1, 1)


def previous_biennium(label: str) -> str:
    """The biennium two years before ``label`` (the rename diff's "before" side, #46)."""
    start = _biennium_start_year(label) - 2
    return f"{start}-{(start + 1) % 100:02d}"


@dataclasses.dataclass(frozen=True)
class RefreshOutcome:
    """Result of one :func:`run_refresh`: the committees summary + the additive
    meeting-discovery and member-cluster upsert counts, so the operator sees each kind
    of work."""

    committees: RunSummary
    meetings_upserted: int
    members_upserted: int = 0


async def run_refresh(
    session: AsyncSession,
    *,
    biennium: str | None = None,
    committee_client: WSLClient | None = None,
    meeting_client: WSLClient | None = None,
    sponsor_client: WSLClient | None = None,
    member_client: WSLClient | None = None,
) -> RefreshOutcome:
    """Execute one WSL refresh cycle against the supplied session.

    Runs the committees discovery, then an **additive** current-biennium meeting-docket
    pull for the Joint/`Other` class (#39), then the **member cluster** (P1b): the
    ``GetSponsors`` roster (Person + party + Senate seat) and a fan-out of
    ``GetActiveCommitteeMembers`` over the current active committees. Returns a
    :class:`RefreshOutcome` with the committees :class:`RunSummary` and the meeting +
    member upsert counts; both the meeting and member phases are **best-effort** — a
    member/meeting-service outage must not fail the (primary) committees refresh (their
    counts are 0 on failure).

    ``committee_client`` / ``meeting_client`` / ``sponsor_client`` / ``member_client``
    are injectable for tests; production defaults to real per-service clients.
    """
    if biennium is None:
        biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())
    current = biennium_for_date(datetime.now(UTC).date())
    if biennium != current:
        # Legitimate only for manual backfills / early-year pins — a stale
        # USA_WA_BIENNIUM left in the timer's env would otherwise redirect the
        # daily discovery to a closed window with no operator-visible signal.
        logger.warning(
            "wsl_refresh_noncurrent_biennium",
            extra={"biennium": biennium, "current_biennium": current},
        )
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    anchors = await bootstrap_synthetic_anchors(
        session, biennium=biennium, jurisdiction_id=jurisdiction.id
    )
    committee_client = committee_client or WSLClient("CommitteeService")
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=jurisdiction.id,
        biennium=biennium,
        client=committee_client,
        meeting_client=meeting_client,
        sponsor_client=sponsor_client,
        member_client=member_client,
        session=session,
    )
    runner = AdapterRunner(
        adapter,
        session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        # Additive discovery (#65): insert newly-appearing committees, but never
        # overwrite an existing row. name/acronym are PM-curated and the read-mirror
        # resolves them; re-writing here would clobber the curation and bump
        # updated_at, winning LWW against PM (the daily ping-pong #65 diagnosed).
        fill_only=True,
    )
    summary = await runner.refresh()
    logger.info(
        "wsl_refresh_summary",
        extra={"summary": dataclasses.asdict(summary), "biennium": biennium},
    )
    meetings_upserted = await _discover_current_meeting_window(runner, biennium)
    members_upserted = await _discover_members(runner, session, biennium, anchors)
    return RefreshOutcome(
        committees=summary,
        meetings_upserted=meetings_upserted,
        members_upserted=members_upserted,
    )


async def _discover_members(
    runner: AdapterRunner,
    session: AsyncSession,
    biennium: str,
    anchors: BootstrapAnchors,
) -> int:
    """Additive member-cluster discovery: the GetSponsors roster + a per-committee
    GetActiveCommitteeMembers fan-out.

    Forced past the cache TTL for the **date-current** biennium (like the meeting window,
    #63) so daily discovery is deterministic; a pinned/backfill ``USA_WA_BIENNIUM`` names
    closed history and stays cache-governed. Each pull is **best-effort** and isolated: the
    sponsor pull and every committee fan-out run inside their own ``begin_nested()``
    SAVEPOINT, so a failure — whether a pre-flush transport error **or** a DB-layer error
    during persist (which would otherwise leave the shared transaction in pending-rollback)
    — rolls back only that pull and the loop continues; neither can abort the rest or fail
    the (primary) committees refresh. The fan-out is **sequential**
    (do-not-parallelize-against-WSL). Archival stays bounded by the runner's unchanged-hash
    dedup. Returns the upsert count.

    The fan-out roster is enumerated from the **DB** — the ``org_type='committee'`` rows
    carrying a ``committees:<biennium>`` GetActiveCommittees citation (the committees phase
    just wrote them), keyed to a House/Senate chamber — rather than a fresh GetActiveCommittees
    pull: no extra SOAP call, and it stays correct on a committees cache-hit (citations
    persist). GetActiveCommitteeMembers is a CommitteeService op scoped to House/Senate
    committees, so the meeting-derived Joint/``Other`` class (chamber = legislature) is
    excluded. Provenance — not ``active`` — is the current-biennium scope (#72): the
    sub-project-3 historical backfill materializes long-dissolved committees as
    ``active=True`` (its default; the reconcile can't demote them past its cohort-floor), so
    an ``active`` scope fanned out over every era — 132 ``No committee was found`` Faults/run
    **and** current members mis-attributed to a historical Id whose name still matches a live
    committee. A ``committees:<biennium>`` citation is the precise "WSL returned this as
    active this biennium" signal; backfill Ids carry only ``committees-roster:*`` provenance
    and are structurally excluded."""
    force = biennium == biennium_for_date(datetime.now(UTC).date())
    total = 0

    try:
        # SAVEPOINT per pull (#1): a DB-layer failure rolls back only this pull, leaving
        # the shared transaction usable for the fan-out below and the committees/meetings
        # work — not just transport errors are contained.
        async with session.begin_nested():
            total += await runner.fetch_and_normalize(
                f"{SPONSORS_RESOURCE_PREFIX}{biennium}", force=force, skip_unchanged=True
            )
    except Exception:
        logger.exception("wsl_sponsors_discovery_failed", extra={"biennium": biennium})

    agency_by_chamber_id = {
        anchors.house_id: "House",
        anchors.senate_id: "Senate",
    }
    # Scope to committees WSL returned as active THIS biennium — those carrying a
    # ``committees:<biennium>`` GetActiveCommittees citation (#72) — and still live (not
    # archived/deleted). Provenance is the precise current signal: the prior ``active=True``
    # proxy fanned out over the historical backfill's dissolved committees (all active=True),
    # wasting SOAP calls and mis-attributing current members to same-named historical Ids.
    current_committee_ids = (
        select(Citation.entity_id)
        .join(FetchEvent, FetchEvent.id == Citation.fetch_event_id)
        .where(
            Citation.entity_type == "organization",
            FetchEvent.resource_id == f"{COMMITTEES_RESOURCE_PREFIX}{biennium}",
        )
    )
    committees = (
        (
            await session.execute(
                select(Organization).where(
                    Organization.source == "usa_wa_legislature",
                    Organization.org_type == "committee",
                    Organization.is_live(),
                    Organization.id.in_(current_committee_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    for committee in committees:
        agency = agency_by_chamber_id.get(committee.parent_organization_id)
        if agency is None:
            continue  # not a House/Senate standing committee (GetActiveCommitteeMembers scope)
        name = committee.short_name
        if not name:
            # A House/Senate committee with no short_name can't be keyed for the members
            # pull — surface it rather than skip silently (the roster key is the WSL Name).
            logger.warning(
                "wsl_committee_members_missing_name",
                extra={"committee_source_id": committee.source_id, "agency": agency},
            )
            continue
        resource_id = committee_members_resource_id(committee.source_id, agency, name)
        try:
            # SAVEPOINT per committee (#1): contains a DB-layer persist failure to this
            # one committee so the rest of the fan-out still commits.
            async with session.begin_nested():
                total += await runner.fetch_and_normalize(
                    resource_id, force=force, skip_unchanged=True
                )
        except Exception:
            logger.exception(
                "wsl_committee_members_discovery_failed",
                extra={"resource_id": resource_id},
            )

    logger.info("wsl_member_discovery", extra={"biennium": biennium, "upserted": total})
    return total


async def _discover_current_meeting_window(runner: AdapterRunner, biennium: str) -> int:
    """Additive Joint/`Other` discovery from the current biennium's meeting window.

    Forced fetch on the stable ``committee-meetings:<begin>:<end>`` id — for the
    date-current biennium only. This pull exists for daily discovery, and the
    source's 24h TTL against the ~24h timer cadence made fetch-vs-skip a jitter
    coin flip (#63), so the live window forces while the committees path stays
    TTL-governed. A non-current biennium (``USA_WA_BIENNIUM`` backfill) is
    immutable history — cache-or-fetch applies, mirroring the harvest's
    never-re-pull stance. Archival stays bounded — the runner skips re-storing an
    unchanged-hash payload (#57/#59), so a static docket costs one FetchEvent row
    per forced run, no RawPayload bytes.
    Absence of a previously-seen body
    from the window is **not** retirement — the meeting normalizer only ever upserts
    the bodies present, never marks an absent one inactive (#39). Best-effort: a
    failure is logged and swallowed (returns 0) so the committees refresh still
    succeeds. Returns the upsert count."""
    resource_id = meetings_resource_id(*biennium_window(biennium))
    # Force only the date-current biennium: its docket is live, so discovery must be
    # deterministic daily. A pinned/backfill biennium (USA_WA_BIENNIUM) is closed,
    # immutable history — cache-or-fetch governs, mirroring the harvest's stance.
    force = biennium == biennium_for_date(datetime.now(UTC).date())
    try:
        # skip_unchanged: a forced re-pull of a byte-identical docket re-records the
        # FetchEvent (TTL/ledger) but doesn't re-normalize — no duplicate Citation set
        # each daily run. A changed docket still re-normalizes (new Joint/`Other` bodies).
        # SAVEPOINT (#1): a DB-layer persist failure rolls back only this pull, so the
        # committees refresh (same transaction) still commits.
        async with runner.session.begin_nested():
            upserted = await runner.fetch_and_normalize(
                resource_id, force=force, skip_unchanged=True
            )
        logger.info(
            "wsl_meeting_discovery",
            extra={
                "biennium": biennium,
                "resource_id": resource_id,
                "upserted": upserted,
                "forced": force,
            },
        )
        return upserted
    except Exception:
        logger.exception("wsl_meeting_discovery_failed", extra={"resource_id": resource_id})
        return 0


async def _main() -> int:
    configure_logging()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2
    engine = create_async_engine(database_url)
    try:
        try:
            async with AsyncSession(engine) as session, session.begin():
                outcome = await run_refresh(session)
        except Exception:
            # Surface the failure cleanly so cron/journal gets a single
            # actionable line plus the traceback in logs, and the process
            # exits 1 (operator-style) instead of dumping a bare traceback.
            logger.exception("wsl_refresh_failed")
            return 1
        committees = outcome.committees
        print(
            f"WSL refresh: committees(discovered={committees.discovered} "
            f"fetched={committees.fetched} "
            f"skipped={committees.skipped_cache_hit} "
            f"upserted={committees.upserted_entities} "
            f"errors={committees.errors}) "
            f"meetings(upserted={outcome.meetings_upserted}) "
            f"members(upserted={outcome.members_upserted})"
        )
        return 0 if committees.errors == 0 else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
