"""C3 committee lifecycle/succession event producer (usa-wa#124).

Emits the objective windows (C1a ``founded``/``dissolved``) + the operator-attested
links (C2 ``succeeded_by``/``split_from``/``merged_with``) to PM as org entity events,
via the partial-success sub-resource (power-map#321).

**Anchor from the mirror, not a local producer row.** PM's event identity is
``(event_type, linked_entity)``; the read-mirror (``sync_entity_events``) keeps a local
``EntityEvent`` per PM event with that identity + its ``pm_entity_event_id``. So the
producer finds its anchor by matching desired identity against the *mirrored* rows —
sidestepping the mirror clobbering a producer-written ``(source, source_id)`` on its next
reconcile (the plan's risk 3). An **anchored** event is refined in place by
``pm_event_id`` (a moved ``event_year`` → ``updated``); an **unanchored** desired event is
created (PM content-dedups an identical re-create to a clock-stable no-op).

**Diff-before-write no-op gate.** An anchored event whose ``event_year`` already matches
is *not* re-emitted — the narrow (year-only) comparator that keeps the LWW ping-pong from
re-arming (the #109 lesson: identity — ``event_type``/``linked_entity`` — is immutable, so
it is never in the comparator). Rejections carry a reason slug (``linked_entity_unresolved``
transient vs terminal), tallied for the #85/#112 telemetry.

Retract of a *stale* link (an operator re-link's superseded predecessor event) is a
deferred follow-up — it needs the produced-ownership signal to retract safely; the common
paths (window emission, link create, date refine) are covered here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Source
from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from clearinghouse_domain_legislative.identity import EntityEvent, Organization
from clearinghouse_sync_powermap.client import DeliveryBlockedError
from clearinghouse_sync_powermap.models import (
    DISPOSITION_REJECTED,
    DISPOSITION_UPDATED,
)
from usa_wa_adapter_legislature.committee_lifecycle import (
    CommitteeWindow,
    collect_committee_presence,
    derive_committee_windows,
)
from usa_wa_adapter_legislature.committee_roster_cohort import CommitteeRosterCohortProvider
from usa_wa_adapter_legislature.committee_succession_store import current_events
from usa_wa_adapter_legislature.refresh import biennium_for_date
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
_ORG_TYPE = "committee"
_ORG_KIND = "organization"

FOUNDED = "founded"
DISSOLVED = "dissolved"


@dataclass
class ProduceStats:
    """Per-run tallies — the daily-refresh + telemetry surface."""

    orgs: int = 0
    submitted: int = 0
    planned: int = 0
    created: int = 0
    updated: int = 0
    noop: int = 0
    rejected: int = 0
    skipped_unanchored_org: int = 0
    skipped_unresolved_link: int = 0
    dry_run: bool = False
    #: rejected reason slug → count (linked_entity_unresolved is transient; the rest terminal).
    reject_reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "orgs": self.orgs,
            "submitted": self.submitted,
            "planned": self.planned,
            "created": self.created,
            "updated": self.updated,
            "noop": self.noop,
            "rejected": self.rejected,
            "skipped_unanchored_org": self.skipped_unanchored_org,
            "skipped_unresolved_link": self.skipped_unresolved_link,
            "dry_run": self.dry_run,
            "reject_reasons": dict(self.reject_reasons),
        }


def build_org_event_items(
    *,
    window: CommitteeWindow | None,
    outgoing_links: Sequence[CommitteeSuccessionEvent],
    linked_pm_ids: dict[str, str | None],
    existing: Sequence[EntityEvent],
) -> tuple[list[dict], int, int]:
    """Desired PM event items for one committee org, diffed against the mirrored events.

    Returns ``(items, noop_count, unresolved_link_count)``. Each item is an
    ``ObservationEventItem`` dict; an anchored item carries ``pm_event_id`` (refine), an
    unanchored one omits it (create). An anchored event whose ``event_year`` already
    matches is dropped as a no-op. A link whose target org has no PM anchor is skipped
    (``linked_pm_ids[target] is None``)."""
    by_identity: dict[tuple[str, str | None], EntityEvent] = {}
    for row in existing:
        linked = str(row.linked_entity_id) if row.linked_entity_id is not None else None
        by_identity[(row.event_type_slug, linked)] = row

    items: list[dict] = []
    noop = 0
    unresolved = 0

    def _emit(slug: str, year: int | None, linked_id: str | None) -> None:
        nonlocal noop
        ex = by_identity.get((slug, linked_id))
        anchor = ex.pm_entity_event_id if ex is not None else None
        if anchor is not None and ex is not None and ex.event_year == year:
            noop += 1
            return
        item: dict[str, Any] = {"event_type_slug": slug}
        if year is not None:
            item["event_year"] = year
        if linked_id is not None:
            item["linked_entity_type"] = "organization"
            item["linked_entity_id"] = linked_id
        if anchor is not None:
            item["pm_event_id"] = str(anchor)
        items.append(item)

    if window is not None:
        if window.founded_year is not None:
            _emit(FOUNDED, window.founded_year, None)
        if window.dissolved_year is not None:
            _emit(DISSOLVED, window.dissolved_year, None)

    for link in outgoing_links:
        linked_pm = linked_pm_ids.get(link.linked_source_id)
        if linked_pm is None:
            unresolved += 1
            continue
        _emit(link.slug, link.effective_year, linked_pm)

    return items, noop, unresolved


async def _committee_orgs(session: AsyncSession) -> dict[str, Organization]:
    """``{source_id: Organization}`` for every produced committee org (anchored or not)."""
    rows = (
        (
            await session.execute(
                select(Organization).where(
                    Organization.source == _SOURCE,
                    Organization.org_type == _ORG_TYPE,
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.source_id: row for row in rows}


async def _mirrored_events(session: AsyncSession, org_id: Any) -> list[EntityEvent]:
    return list(
        (
            await session.execute(
                select(EntityEvent).where(
                    EntityEvent.entity_kind == _ORG_KIND,
                    EntityEvent.entity_id == org_id,
                )
            )
        )
        .scalars()
        .all()
    )


async def produce_committee_events(
    session: AsyncSession,
    pm_client: Any,
    *,
    windows: dict[str, CommitteeWindow],
    links: Sequence[CommitteeSuccessionEvent],
    dry_run: bool = False,
) -> ProduceStats:
    """Emit each committee org's window + outgoing links to PM (create/refine, no-op gated).

    ``windows`` is the C1a per-``Id`` map; ``links`` the C2 current attestations. PM is
    authority for events and mirrors them back, so no local write here — the anchors come
    from the mirror. ``dry_run`` computes the diff (counts intended items as ``planned``)
    without posting. Returns a :class:`ProduceStats`."""
    orgs = await _committee_orgs(session)
    pm_id_by_source = {
        sid: (str(o.pm_organization_id) if o.pm_organization_id else None)
        for sid, o in orgs.items()
    }
    links_by_subject: dict[str, list[CommitteeSuccessionEvent]] = {}
    for link in links:
        links_by_subject.setdefault(link.subject_source_id, []).append(link)

    stats = ProduceStats(dry_run=dry_run)
    subjects = set(windows) | set(links_by_subject)
    for source_id in sorted(subjects):
        org = orgs.get(source_id)
        if org is None or org.pm_organization_id is None:
            stats.skipped_unanchored_org += 1
            logger.warning("committee_event_org_unanchored", extra={"source_id": source_id})
            continue
        existing = await _mirrored_events(session, org.id)
        items, noop, unresolved = build_org_event_items(
            window=windows.get(source_id),
            outgoing_links=links_by_subject.get(source_id, []),
            linked_pm_ids=pm_id_by_source,
            existing=existing,
        )
        stats.noop += noop
        stats.skipped_unresolved_link += unresolved
        if not items:
            continue
        stats.orgs += 1
        if dry_run:
            stats.planned += len(items)
            continue
        stats.submitted += len(items)
        results = await pm_client.submit_org_event_observations(org.pm_organization_id, items)
        for result in results:
            if result.rejected:
                stats.rejected += 1
                reason = result.reason or "unknown"
                stats.reject_reasons[reason] = stats.reject_reasons.get(reason, 0) + 1
                logger.warning(
                    "committee_event_rejected",
                    extra={"source_id": source_id, "reason": reason},
                )
            elif result.disposition == DISPOSITION_UPDATED:
                stats.updated += 1
            elif result.disposition != DISPOSITION_REJECTED and result.anchored:
                stats.created += 1
    logger.info("committee_events_produced", extra=stats.as_dict())
    return stats


async def _build_inputs(
    session: AsyncSession, wsl_client: Any, biennium: str
) -> tuple[dict[str, CommitteeWindow], Sequence[CommitteeSuccessionEvent]]:
    """Assemble the C1a windows (from the roster archive) + C2 current links."""
    source = (
        await session.execute(select(Source).where(Source.slug == _SOURCE))
    ).scalar_one_or_none()
    provider = CommitteeRosterCohortProvider(
        wsl_client, session=session, source_id=(source.id if source else None)
    )
    presence = await collect_committee_presence(provider)
    archived = await provider.archived_bienniums()
    windows = derive_committee_windows(
        presence, current_biennium=biennium, archived_bienniums=archived
    )
    links = await current_events(session)
    return windows, links


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.committee_event_producer",
        description=(
            "Emit committee lifecycle windows (founded/dissolved) + operator succession "
            "links to PM as org entity events (usa-wa#124 C3)."
        ),
    )
    parser.add_argument(
        "--biennium", default=None, help="Biennium label; default USA_WA_BIENNIUM/date."
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute the diff without posting.")
    return parser


def _resolve_biennium(arg: str | None) -> str:
    if arg:
        return arg
    return os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())


async def _run(args: argparse.Namespace) -> dict:
    biennium = _resolve_biennium(args.biennium)
    settings = get_sidecar_settings()
    factory = get_session_factory()
    wsl_client = WSLClient("CommitteeService")
    if args.dry_run:
        async with factory() as session:
            windows, links = await _build_inputs(session, wsl_client, biennium)
            stats = await produce_committee_events(
                session, None, windows=windows, links=links, dry_run=True
            )
            return stats.as_dict()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to submit observations.")
    pm_client = build_pm_client(settings)
    try:
        async with factory() as session:
            windows, links = await _build_inputs(session, wsl_client, biennium)
            stats = await produce_committee_events(session, pm_client, windows=windows, links=links)
            return stats.as_dict()
    finally:
        await pm_client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Exit 0 clean/dry-run; 1 if any event rejected; 2 on a global auth block."""
    configure_logging()
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except DeliveryBlockedError as exc:
        json.dump(
            {"error": "delivery blocked — check POWERMAP_API_KEY", "detail": str(exc)}, sys.stdout
        )
        sys.stdout.write("\n")
        return 2
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 1 if result.get("rejected", 0) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
