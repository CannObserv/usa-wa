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

**Retract of a stale link (#127).** A superseded attestation (an operator re-link or
successor change) whose ``(subject, slug, linked)`` identity is *not* reasserted by an
active link has its still-anchored mirror event retracted (``op=retract``) — the ownership
signal is the superseded :class:`CommitteeSuccessionEvent` itself. A ``retracted_at`` stamp
on the mirror row guards the retract against re-firing until the read-mirror prunes the
archived PM event; a year-only correction keeps the identity and refines in place instead.
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
from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from clearinghouse_domain_legislative.identity import EntityEvent, Organization
from clearinghouse_domain_legislative.terms import biennium_for_date
from clearinghouse_sync_powermap.client import DeliveryBlockedError
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_UPDATED,
)
from usa_wa_adapter_legislature.cohorts import committee_roster_provider
from usa_wa_adapter_legislature.committees.lifecycle import (
    CommitteeWindow,
    build_founded_floors,
    collect_committee_presence,
    derive_committee_windows,
)
from usa_wa_adapter_legislature.committees.succession_store import (
    current_events,
    superseded_events,
)
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
    #: PM content-dedup'd an unanchored re-send to an existing event (``auto-attached``) —
    #: it created nothing (mirror-lag re-send), kept distinct from ``created`` telemetry.
    reobserved: int = 0
    updated: int = 0
    noop: int = 0
    rejected: int = 0
    #: op=retract emitted for a superseded, unreasserted, still-anchored event (#127).
    retracted: int = 0
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
            "reobserved": self.reobserved,
            "updated": self.updated,
            "noop": self.noop,
            "rejected": self.rejected,
            "retracted": self.retracted,
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
    #: Identities whose mirror event PM has archived (we retracted it). #322: a retracted
    #: anchor is terminal — re-observing only auto-attaches to the archive, never resurrects,
    #: so a reasserted identity is left alone rather than churning a pointless re-observe.
    retracted_identities: set[tuple[str, str | None]] = set()
    for row in existing:
        linked = str(row.linked_entity_id) if row.linked_entity_id is not None else None
        if getattr(row, "retracted_at", None) is not None:
            retracted_identities.add((row.event_type_slug, linked))
            continue
        by_identity[(row.event_type_slug, linked)] = row

    items: list[dict] = []
    noop = 0
    unresolved = 0

    def _emit(slug: str, year: int | None, linked_id: str | None) -> None:
        nonlocal noop
        if (slug, linked_id) in retracted_identities:
            return  # terminal retracted anchor — do not re-emit
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


def build_retract_items(
    *,
    superseded_links: Sequence[CommitteeSuccessionEvent],
    active_links: Sequence[CommitteeSuccessionEvent],
    linked_pm_ids: dict[str, str | None],
    existing: Sequence[EntityEvent],
) -> list[tuple[dict, EntityEvent]]:
    """Retract items for one org's superseded, unreasserted, still-anchored events (#127).

    A superseded attestation whose ``(slug, linked_pm)`` identity is **not** reasserted by
    any active link (a re-link or drop, not a year-only correction — which keeps the
    identity and refines instead) and whose mirror :class:`EntityEvent` is anchored and not
    already ``retracted_at`` yields one ``{op: retract, pm_event_id}`` item paired with the
    row to stamp on success. Idempotent: an already-retracted row is skipped."""
    active_identities: set[tuple[str, str | None]] = set()
    for link in active_links:
        linked_pm = linked_pm_ids.get(link.linked_source_id)
        if linked_pm is not None:
            active_identities.add((link.slug, linked_pm))

    # Skip already-retracted rows up front (idempotency) — symmetric with the
    # ``retracted_identities`` handling in :func:`build_org_event_items`.
    by_identity: dict[tuple[str, str | None], EntityEvent] = {}
    for row in existing:
        if getattr(row, "retracted_at", None) is not None:
            continue
        linked = str(row.linked_entity_id) if row.linked_entity_id is not None else None
        by_identity[(row.event_type_slug, linked)] = row

    out: list[tuple[dict, EntityEvent]] = []
    seen_anchors: set[str] = set()
    for link in superseded_links:
        linked_pm = linked_pm_ids.get(link.linked_source_id)
        if linked_pm is None:
            continue
        identity = (link.slug, linked_pm)
        if identity in active_identities:
            continue  # year-only correction — the active link refines this in place
        row = by_identity.get(identity)
        if row is None or row.pm_entity_event_id is None:
            continue
        anchor = str(row.pm_entity_event_id)
        if anchor in seen_anchors:
            continue
        seen_anchors.add(anchor)
        # PM validates event_type + linked_entity on every item, retract included — carry
        # the full identity (addressed by pm_event_id) so the 422 schema check passes.
        item: dict[str, Any] = {
            "op": "retract",
            "pm_event_id": anchor,
            "event_type_slug": link.slug,
            "linked_entity_type": "organization",
            "linked_entity_id": linked_pm,
        }
        out.append((item, row))
    return out


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
    superseded_links: Sequence[CommitteeSuccessionEvent] = (),
    dry_run: bool = False,
) -> ProduceStats:
    """Emit each committee org's window + outgoing links to PM (create/refine, no-op gated).

    ``windows`` is the C1a per-``Id`` map; ``links`` the C2 current attestations;
    ``superseded_links`` the corrected/re-linked attestations whose stale PM event is
    retracted (#127) unless an active link still asserts the same identity. PM is authority
    for events and mirrors them back, so the only local write is the ``retracted_at`` stamp
    (guards a retract against re-firing). ``dry_run`` computes the diff (counts intended
    items as ``planned``) without posting. Returns a :class:`ProduceStats`."""
    orgs = await _committee_orgs(session)
    pm_id_by_source = {
        sid: (str(o.pm_organization_id) if o.pm_organization_id else None)
        for sid, o in orgs.items()
    }
    links_by_subject: dict[str, list[CommitteeSuccessionEvent]] = {}
    for link in links:
        links_by_subject.setdefault(link.subject_source_id, []).append(link)
    superseded_by_subject: dict[str, list[CommitteeSuccessionEvent]] = {}
    for link in superseded_links:
        superseded_by_subject.setdefault(link.subject_source_id, []).append(link)

    stats = ProduceStats(dry_run=dry_run)
    stamped = False
    subjects = set(windows) | set(links_by_subject) | set(superseded_by_subject)
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
        retracts = build_retract_items(
            superseded_links=superseded_by_subject.get(source_id, []),
            active_links=links_by_subject.get(source_id, []),
            linked_pm_ids=pm_id_by_source,
            existing=existing,
        )
        stats.noop += noop
        stats.skipped_unresolved_link += unresolved
        all_items = items + [item for item, _ in retracts]
        if not all_items:
            continue
        stats.orgs += 1
        if dry_run:
            stats.planned += len(all_items)
            continue
        stats.submitted += len(all_items)
        results = await pm_client.submit_org_event_observations(org.pm_organization_id, all_items)
        # PM's sub-resource contract is one result per submitted event, in request order —
        # we slice on that to separate create/refine from retract outcomes. A count mismatch
        # would silently drop the tail (retracts un-stamped, safely retried next run), so
        # surface the contract violation rather than let it pass unnoticed.
        if len(results) != len(all_items):
            logger.warning(
                "committee_event_result_count_mismatch",
                extra={
                    "source_id": source_id,
                    "submitted": len(all_items),
                    "returned": len(results),
                },
            )
        # Results are in request order: the create/refine block first, then the retracts.
        for result in results[: len(items)]:
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
            elif result.disposition == DISPOSITION_AUTO_ATTACHED and result.anchored:
                stats.reobserved += 1
            elif result.anchored:  # DISPOSITION_NEW — a genuine create
                stats.created += 1
        for result, (_, row) in zip(results[len(items) :], retracts):
            if result.rejected:
                stats.rejected += 1
                reason = result.reason or "unknown"
                stats.reject_reasons[reason] = stats.reject_reasons.get(reason, 0) + 1
                logger.warning(
                    "committee_event_retract_rejected",
                    extra={"source_id": source_id, "reason": reason},
                )
            else:  # DISPOSITION_RETRACTED (or any non-rejected) — PM archived it
                stats.retracted += 1
                row.retracted_at = datetime.now(UTC)
                stamped = True
    if stamped:
        await session.flush()
    # Wrap under one key: as_dict() has a ``created`` field, which collides with the
    # reserved ``LogRecord.created`` attribute and would raise once logging is configured.
    logger.info("committee_events_produced", extra={"stats": stats.as_dict()})
    return stats


async def _build_inputs(
    session: AsyncSession, biennium: str
) -> tuple[
    dict[str, CommitteeWindow],
    Sequence[CommitteeSuccessionEvent],
    Sequence[CommitteeSuccessionEvent],
]:
    """Assemble the C1a windows (from the roster archive) + C2 current + superseded links."""
    provider = await committee_roster_provider(session)
    presence = await collect_committee_presence(provider)
    archived = await provider.archived_bienniums()
    links = await current_events(session)
    superseded = await superseded_events(session)
    # Back-stamp correction (#128): bump a re-keyed committee's founded to its attested
    # rename year, off the WSL back-stamped prior biennium.
    windows = derive_committee_windows(
        presence,
        current_biennium=biennium,
        archived_bienniums=archived,
        founded_floors=build_founded_floors(links),
    )
    return windows, links, superseded


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
    if args.dry_run:
        async with factory() as session:
            windows, links, superseded = await _build_inputs(session, biennium)
            stats = await produce_committee_events(
                session,
                None,
                windows=windows,
                links=links,
                superseded_links=superseded,
                dry_run=True,
            )
            return stats.as_dict()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to submit observations.")
    pm_client = build_pm_client(settings)
    try:
        async with factory() as session:
            windows, links, superseded = await _build_inputs(session, biennium)
            stats = await produce_committee_events(
                session, pm_client, windows=windows, links=links, superseded_links=superseded
            )
            # Persist the retracted_at stamps (the run's only local write, #127).
            await session.commit()
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
