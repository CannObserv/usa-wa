"""Producer-side Joint/`Other` committee **rename** detection — meeting-derived (#56).

Sibling of :mod:`reconcile_committee_names` (#46) for the committee class
``CommitteeService.GetCommittees`` is structurally blind to (#39): Joint/`Other` bodies
exist only in each meeting's nested committee refs. #46's roster diff therefore never sees
their renames — a live instance is **ESEC (`Id 13945`)**, renamed by 2023 c 230 s 308,
visible only via ``CommitteeMeetingService.GetCommitteeMeetings``.

The rename signal and the windowed dated-name emit are identical to #46 (and shared via
:func:`~usa_wa_sync_powermap.committee_name_reconcile.reconcile_names_from_maps`); three
things differ:

1. **Source.** The current/prior cohorts come from two bienniums'
   ``GetCommitteeMeetings`` windows (deduped Joint/`Other` refs by stable ``Id``), via
   :class:`~usa_wa_adapter_legislature.meeting_cohort.MeetingCohortProvider`, governing the
   local ``org_type='other'`` class. The diff intersects ``Id``s present in **both** windows
   — a body absent from one window is dormancy, never a rename.

2. **Clean name.** The cohort name is WSL's clean ``Name`` (#61 ``observed_name``), not the
   agency-double-prefixed ``LongName`` stored as ``Organization.name`` ("Joint Joint …").
   The same clean string is diffed and emitted, so the double-prefix never reaches PM and a
   PM canonicalisation can't false-fire.

3. **Relaxed guards.** Meeting cohorts are dormancy-prone, so the low-overlap guard (which
   in #46 assumes near-total roster overlap) is **off by default**
   (``DEFAULT_MIN_OVERLAP_FRACTION = 0.0``); and the rename-storm fraction only applies once
   the overlap reaches :data:`DEFAULT_STORM_FLOOR_MIN_OVERLAP` (a tiny overlap makes the
   fraction hair-trigger). Empty-pull abort is kept.

Emit-to-PM-only, no operator token (shell access is the trust boundary), ``--dry-run``
previews. Exit codes match #46: ``0`` clean / ``1`` some rows rejected-or-failed / ``2``
auth block / ``3`` guardrail abort.

Examples::

    python -m usa_wa_sync_powermap.reconcile_committee_meeting_names --dry-run
    python -m usa_wa_sync_powermap.reconcile_committee_meeting_names --biennium 2025-26
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Source
from clearinghouse_sync_powermap.client import DeliveryBlockedError
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from usa_wa_adapter_legislature.meeting_cohort import MeetingCohortProvider
from usa_wa_adapter_legislature.refresh import (
    biennium_for_date,
    biennium_start_date,
    previous_biennium,
)
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_sync_powermap.committee_name_reconcile import (
    DEFAULT_MAX_RENAME_FRACTION,
    DEFAULT_MIN_OVERLAP_FRACTION,
    EXIT_ABORTED,
    reconcile_names_from_maps,
)
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor

logger = get_logger(__name__)

#: Local ``org_type`` of the rows this diff governs (the meeting-derived Joint/`Other` class).
_ORG_TYPE = "other"
#: Low-overlap floor for this dormancy-prone class — the shared spine's relaxed **low non-zero**
#: default (:data:`~usa_wa_sync_powermap.committee_name_reconcile.DEFAULT_MIN_OVERLAP_FRACTION`),
#: re-exported here so the CLI default and the spine default are the one constant. A
#: dormancy-thinned meeting overlap passes; a fully-disjoint / badly-wrong-biennium pull still
#: aborts rather than reading as a clean "renamed: 0". Operator-settable
#: (``--min-overlap-fraction``).
#: Storm-floor minimum overlap: the rename-storm *fraction* is only weighed once the overlap
#: reaches this many bodies. Below it the fraction is meaningless (one rename of two is
#: 0.5), which would hair-trigger the abort on the small overlaps dormancy produces.
DEFAULT_STORM_FLOOR_MIN_OVERLAP = 5


async def reconcile_committee_meeting_names(
    session: AsyncSession,
    descriptor: EntityDescriptor,
    cohort_provider: Any,
    pm_client: Any,
    *,
    biennium: str,
    dry_run: bool = False,
    max_rename_fraction: float = DEFAULT_MAX_RENAME_FRACTION,
    min_overlap_fraction: float = DEFAULT_MIN_OVERLAP_FRACTION,
    storm_floor_min_overlap: int = DEFAULT_STORM_FLOOR_MIN_OVERLAP,
) -> dict:
    """Diff two bienniums' meeting-derived Joint/`Other` cohorts on the stable ``Id`` and
    emit windowed dated-name evidence for each body whose (normalized) clean ``Name`` changed.

    Builds the current/prior ``{Id: clean Name}`` cohorts from ``cohort_provider`` (a
    :class:`~usa_wa_adapter_legislature.meeting_cohort.MeetingCohortProvider`) and hands them
    to the shared
    :func:`~usa_wa_sync_powermap.committee_name_reconcile.reconcile_names_from_maps` spine
    with ``org_type='other'`` and the re-tuned guard defaults. Guardrails, per-row
    eligibility, and emit-to-PM-only semantics: see the spine. ``dry_run`` still fetches both
    cohorts (so the diff/guards run) but posts nothing."""
    prior_label = previous_biennium(biennium)
    current = await cohort_provider.cohort(biennium)
    prior = await cohort_provider.cohort(prior_label)
    return await reconcile_names_from_maps(
        session,
        descriptor,
        pm_client,
        current=current,
        prior=prior,
        biennium=biennium,
        prior_biennium=prior_label,
        boundary=biennium_start_date(biennium),
        org_type=_ORG_TYPE,
        dry_run=dry_run,
        max_rename_fraction=max_rename_fraction,
        min_overlap_fraction=min_overlap_fraction,
        storm_floor_min_overlap=storm_floor_min_overlap,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.reconcile_committee_meeting_names",
        description=(
            "Detect Joint/Other (meeting-derived) committee renames across a biennium "
            "boundary and emit windowed dated-name evidence to PM (#56)."
        ),
    )
    parser.add_argument(
        "--biennium",
        default=None,
        help="Current biennium label (e.g. 2025-26). Defaults to USA_WA_BIENNIUM or today.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the diff and guardrails without posting any observation.",
    )
    parser.add_argument(
        "--max-rename-fraction",
        type=float,
        default=DEFAULT_MAX_RENAME_FRACTION,
        help=(
            "Abort if more than this fraction of a sufficiently-large overlapping cohort "
            f"shows a changed name (default {DEFAULT_MAX_RENAME_FRACTION})."
        ),
    )
    parser.add_argument(
        "--min-overlap-fraction",
        type=float,
        default=DEFAULT_MIN_OVERLAP_FRACTION,
        help=(
            "Abort if the two cohorts share fewer than this fraction of the smaller cohort's "
            f"Ids (default {DEFAULT_MIN_OVERLAP_FRACTION} = off; dormancy makes thin meeting "
            "overlaps normal). Raise it to re-arm a floor."
        ),
    )
    parser.add_argument(
        "--storm-floor-min-overlap",
        type=int,
        default=DEFAULT_STORM_FLOOR_MIN_OVERLAP,
        help=(
            "Only weigh the rename-storm fraction once the overlap reaches this many bodies "
            f"(default {DEFAULT_STORM_FLOOR_MIN_OVERLAP}); below it the fraction is "
            "meaningless on a dormancy-thinned cohort."
        ),
    )
    return parser


def _resolve_biennium(arg: str | None) -> str:
    if arg:
        return arg
    return os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())


async def _resolve_source_id(session: AsyncSession) -> Any:
    """The provenance ``Source.id`` for the WSL legislature source, or ``None`` (read-only).

    Lets the cohort provider read closed windows from the archive the refresh/harvest wrote.
    ``None`` (source not yet provisioned) just means the provider falls back to live pulls."""
    return (
        await session.execute(select(Source.id).where(Source.slug == "usa_wa_legislature"))
    ).scalar_one_or_none()


async def _make_provider(session: AsyncSession) -> MeetingCohortProvider:
    """A cache-aware provider bound to ``session`` + the resolved WSL source, so closed
    meeting windows are re-parsed from the archive instead of re-pulled from WSL."""
    return MeetingCohortProvider(
        WSLClient("CommitteeMeetingService"),
        session=session,
        source_id=await _resolve_source_id(session),
    )


async def _run(args: argparse.Namespace) -> dict:
    """Open a session + meeting-cohort/PM clients, run the reconciliation, return the summary.

    A ``dry_run`` still needs the cohort provider (to obtain both windows) but no PM client.
    Emit-to-PM-only, so no commit. The provider is built inside the session so it can serve
    closed windows from the archive (cache-first)."""
    biennium = _resolve_biennium(args.biennium)
    settings = get_sidecar_settings()
    factory = get_session_factory()
    if args.dry_run:
        async with factory() as session:
            return await reconcile_committee_meeting_names(
                session,
                OrganizationDescriptor(),
                await _make_provider(session),
                None,
                biennium=biennium,
                dry_run=True,
                max_rename_fraction=args.max_rename_fraction,
                min_overlap_fraction=args.min_overlap_fraction,
                storm_floor_min_overlap=args.storm_floor_min_overlap,
            )
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to submit observations.")
    pm_client = GeneratedPowerMapClient(settings.powermap_base_url, settings.powermap_api_key)
    try:
        async with factory() as session:
            return await reconcile_committee_meeting_names(
                session,
                OrganizationDescriptor(),
                await _make_provider(session),
                pm_client,
                biennium=biennium,
                max_rename_fraction=args.max_rename_fraction,
                min_overlap_fraction=args.min_overlap_fraction,
                storm_floor_min_overlap=args.storm_floor_min_overlap,
            )
    finally:
        await pm_client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the reconciliation, and print the summary as JSON.

    Exit codes: ``0`` clean (or dry-run); :data:`EXIT_ABORTED` (3) a guardrail abort (took no
    action); ``1`` ran but some rows rejected/failed; ``2`` a global auth block."""
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
    if result.get("aborted"):
        return EXIT_ABORTED
    return 1 if (result.get("rejected", 0) or result.get("failed", 0)) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
