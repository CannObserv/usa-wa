"""One-shot migration: collapse per-biennium committee memberships into spans (#82).

Before #82 the daily refresh emitted one membership Assignment per member **per biennium**
per committee (``{member}:committee:{committee_id}:{biennium}``). The span builder now emits
one row per contiguous membership tenure, keyed on its **start** biennium — the *same* 4-part
shape.

**Why that shape collision is mostly a gift.** A span that starts at a legacy row's biennium
has an *identical* ``source_id``, so :func:`~span_emit._upsert_assignment` updates that row in
place — keeping its ``id`` and its ``pm_assignment_id``. On a shallow archive (only the current
biennium pulled) every legacy row simply becomes its own span. Nothing to migrate.

The migration matters once the Phase A harvest **deepens** a span: a member on Appropriations
since 2013-14 gets the span ``…:committee:31635:2013-14``, while the shipped legacy row
``…:committee:31635:2025-26`` is stranded — a second live row for the same membership. This
CLI retires those.

**Legacy = a committee Assignment whose source_id is not one of the emitted span keys.** The
shape can't distinguish them (unlike #78-3's 3-part-vs-4-part party/Senate keys), so the span
set itself is the discriminator. Each stranded row is mapped to the span covering its biennium
(same ``(person_id, role_id)``, validity window containing the row's ``valid_from``) — the
committee ``member`` Role is per-committee, so ``(person, role)`` names the membership. The PM
anchor moves to the span; the legacy row + its citations are hard-deleted.

**Owner role.** Retiring a row deletes its ``citations``, which the app role is REVOKEd (#54),
so this runs under ``DATABASE_URL_OWNER`` — like :mod:`migrate_sponsor_spans`. The daily span
re-drive stays app-role-safe (insert-only citations).

**Deploy sequencing.** Run this in the *same* maintenance window as the Phase A harvest that
deepens the spans, with ``usa-wa-sync-powermap`` paused. A deepened span is a new local row
with a new ``valid_from``, and PM keys assignments on ``(person, role, start_date)`` — so if
the sidecar drains before this runs, it mints a **fresh** PM assignment for the span while the
legacy row still holds the old one. This migration can then only retire the legacy row, not
transfer its anchor: that PM assignment is orphaned upstream (a live PM row with the wrong
``start_date`` and no local mirror to ever correct it). That case is counted as
``anchors_dropped`` and warned per row, so it is visible rather than silent — but the only way
to avoid it is to run harvest → migrate before the sidecar sees the deepened spans. Correcting
already-orphaned PM assignments is the start-date-correction gap tracked in #80. A second
reason to keep the window tight: the daily #83 stale-span sweep
(:func:`~usa_wa_adapter_legislature.span_emit.close_stale_spans`) treats a stranded legacy row
as an unasserted open span and closes it — harmless for the mapping here (it matches on the
unchanged ``valid_from``), but the row's PM anchor transiently asserts ``is_active=false``
until the anchor transfers.

Idempotent: a second pass finds no stranded rows. ``--dry-run`` rolls back.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Citation
from clearinghouse_domain_legislative.identity import Assignment
from usa_wa_adapter_legislature.committee_member_cohort import CommitteeMemberCohortProvider
from usa_wa_adapter_legislature.committee_membership_observations import (
    KIND_COMMITTEE,
    build_committee_membership_observations,
)
from usa_wa_adapter_legislature.committee_span_emit import emit_committee_spans
from usa_wa_adapter_legislature.provisioning import get_or_create_source, resolve_jurisdiction
from usa_wa_adapter_legislature.span_emit import ASSIGNMENT_CITATION_TYPE, SOURCE
from usa_wa_adapter_legislature.synthesis import biennium_for_date
from usa_wa_adapter_legislature.tenure_spans import build_tenure_spans
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)


@dataclass(frozen=True)
class MigrationResult:
    """Counts from one migration pass."""

    spans_built: int
    legacy_found: int
    anchors_transferred: int
    legacy_retired: int
    orphans_no_span: int
    #: Legacy rows retired while their covering span already carried a *different* PM anchor:
    #: the legacy row's PM assignment is orphaned upstream (see the module docstring's deploy
    #: sequencing note). Expected 0 when harvest+migrate run before the sidecar drains.
    anchors_dropped: int = 0


def _is_committee_assignment(source_id: str) -> bool:
    """``{member}:committee:{committee_id}:{biennium}`` — 4 parts, dim ``committee``."""
    parts = source_id.split(":")
    return len(parts) == 4 and parts[1] == KIND_COMMITTEE


def _covering_span(
    spans: Sequence[Assignment], legacy_valid_from: date | None
) -> Assignment | None:
    """The span (among candidates sharing the legacy row's person+role) whose validity window
    contains the legacy biennium's start — so a stranded row collapses into the tenure it
    actually belonged to, not a different (e.g. closed) run on the same committee."""
    if legacy_valid_from is None:
        return None
    for span in spans:
        upper = span.valid_to or date.max
        if span.valid_from <= legacy_valid_from <= upper:
            return span
    return None


async def migrate_committee_spans(
    session: AsyncSession,
    *,
    current_biennium: str | None = None,
    member_client: WSLClient | None = None,
) -> MigrationResult:
    """Build the membership spans, then retire any per-biennium committee row they stranded."""
    jurisdiction = await resolve_jurisdiction(session)
    source = await get_or_create_source(session, jurisdiction)
    current = current_biennium or biennium_for_date(datetime.now(UTC).date())

    provider = CommitteeMemberCohortProvider(
        member_client or WSLClient("CommitteeService"), session=session, source_id=source.id
    )
    rosters = await provider.archived_rosters()
    spans = build_tenure_spans(
        build_committee_membership_observations(rosters), current_biennium=current
    )
    fetch_events = await provider.fetch_event_map()
    spans_built = await emit_committee_spans(
        session, spans, reliability=source.reliability, fetch_events=fetch_events
    )
    span_source_ids = {span.source_id for span in spans}

    live = (
        (
            await session.execute(
                select(Assignment).where(
                    Assignment.source == SOURCE, Assignment.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    # The emitted span rows, indexed by the membership's structural key. A member with
    # non-contiguous tenure on one committee has several spans under one (person, role), so a
    # stranded row must map to the span whose window covers ITS biennium.
    spans_by_key: dict[tuple, list[Assignment]] = defaultdict(list)
    for row in live:
        if row.source_id in span_source_ids:
            spans_by_key[(row.person_id, row.role_id)].append(row)
    # Legacy = a committee row the span set does NOT claim (shape can't tell them apart).
    legacy = [
        row
        for row in live
        if _is_committee_assignment(row.source_id) and row.source_id not in span_source_ids
    ]

    transferred = retired = orphans = dropped = 0
    for row in legacy:
        span = _covering_span(spans_by_key.get((row.person_id, row.role_id), ()), row.valid_from)
        if span is None:
            logger.warning(
                "committee_span_migrate_no_successor",
                extra={"source_id": row.source_id, "person_id": str(row.person_id)},
            )
            orphans += 1
            continue
        anchor = row.pm_assignment_id
        # Index-safe (#95, mirroring migrate_pdc_spans._retire_onto #91): delete the stranded
        # row — freeing its anchor — and flush BEFORE moving the anchor to the keeper. Assigning
        # first would autoflush the keeper's UPDATE while the legacy row still holds the same
        # pm_assignment_id, colliding with uq_assignments_pm_assignment_id (#86).
        await session.execute(
            delete(Citation).where(
                Citation.entity_type == ASSIGNMENT_CITATION_TYPE, Citation.entity_id == row.id
            )
        )
        await session.delete(row)
        await session.flush()
        retired += 1
        if anchor is not None:
            if span.pm_assignment_id is None:
                span.pm_assignment_id = anchor
                await session.flush()
                transferred += 1
            elif span.pm_assignment_id != anchor:
                # The sidecar anchored the deepened span before this ran; retiring the legacy
                # row strands its PM assignment upstream. Surface it (see the deploy note).
                logger.warning(
                    "committee_span_migrate_anchor_dropped",
                    extra={
                        "source_id": row.source_id,
                        "orphaned_pm_assignment_id": str(anchor),
                        "span_pm_assignment_id": str(span.pm_assignment_id),
                    },
                )
                dropped += 1
    await session.flush()

    result = MigrationResult(
        spans_built=spans_built,
        legacy_found=len(legacy),
        anchors_transferred=transferred,
        legacy_retired=retired,
        orphans_no_span=orphans,
        anchors_dropped=dropped,
    )
    logger.info("committee_span_migrate_complete", extra=result.__dict__)
    return result


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Collapse per-biennium committee memberships into merged spans (#82)."
    )
    parser.add_argument("--dry-run", action="store_true", help="migrate but roll back (preview)")
    args = parser.parse_args(argv)

    # Owner role: retiring a stranded row deletes its citations, which the app role is
    # REVOKEd on the provenance ledger (#54). Same contract as migrate_sponsor_spans.
    database_url = os.environ.get("DATABASE_URL_OWNER")
    if not database_url:
        print(
            "DATABASE_URL_OWNER is not set; aborting — retiring legacy rows deletes their "
            "citations, which the app role is REVOKEd (#54); run under the owner role.",
            file=sys.stderr,
        )
        return 2

    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine) as session:
            result = await migrate_committee_spans(session)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception:
        logger.exception("committee_span_migrate_failed")
        return 1
    finally:
        await engine.dispose()

    print(
        f"Committee span migration: legacy_found={result.legacy_found} "
        f"anchors_transferred={result.anchors_transferred} retired={result.legacy_retired} "
        f"anchors_dropped={result.anchors_dropped} "
        f"orphans_no_span={result.orphans_no_span} spans_built={result.spans_built} "
        f"{'(dry-run, rolled back)' if args.dry_run else '(committed)'}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(asyncio.run(_main()))
