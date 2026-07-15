"""Producer-side detection of WSL committee **renames** across a biennium boundary (#46).

A committee keeps a stable WSL ``Id`` while its ``LongName`` changes — usually at a new
biennium. PM models a name's validity as an ``OrgName`` window
(``[effective_start, effective_end)``, power-map#239); the #45 read mirror brings those
windows back into ``organization_names``. This module is the **write-side sibling**: the
deliberate producer action that *emits* the window when WSL reveals a rename.

The rename is a diff of **WSL's own rosters**, joined on the stable ``Id``:

    rename(Id) ⇔ normalize_name(GetCommittees(prior)[Id].LongName)
                 ≠ normalize_name(GetCommittees(current)[Id].LongName)

Diffing WSL's raw ``LongName`` (normalized) — **not** the locally-held
``Organization.name`` scalar — is the load-bearing choice. That scalar is PM-resolved and
curated (``upsert_from_pm`` overwrites it with PM's canonical name), so diffing against it
would (a) fire on PM's punctuation/word-order canonicalisation as a false rename and
(b) miss a rename that already round-tripped through PM. ``normalize_name`` equality is the
precision gate (same folding the org match cascade uses), so ``Ways & Means`` ⇄
``Ways and Means`` is not a rename.

The biennium boundary supplies the window: the current biennium's start date is the new
name's ``effective_start`` and the prior name's ``effective_end`` (half-open). The prior
name's *start* is unknown (possibly bienniums old) and is left to PM.

**Emit-to-PM-only** (decision #2 in the #45 plan): PM curates ``is_canonical`` and resolves
the canonical scalar; the #45 read mirror brings the windowed rows back. No local write —
so the produced cohort's former-name association only lands locally after PM accepts the
evidence and the sidecar mirrors it back (a propagation lag, accepted).

Guardrails mirror the #44 ``reconcile_committee_active`` sibling:

- **Empty-pull abort.** Either roster empty reads as a failed pull, never a real diff — an
  empty *current* would window nothing; an empty *prior* would make every committee look
  brand-new and mis-window. Abort.
- **Low-overlap abort.** WSL committee ``Id``s are stable across bienniums, so a healthy
  diff overlaps near-totally. Too thin a shared-``Id`` overlap (``--min-overlap-fraction``,
  measured against the *smaller* roster so one-sided growth/drop doesn't false-trip) means a
  wrong-biennium pull or an Id-scheme change — a meaningless diff that would otherwise slip
  past the other guards (``renamed=0`` can't trip the storm floor) and read as a clean "no
  renames". Abort.
- **Rename-storm floor.** A renamed fraction over ``--max-rename-fraction`` reads as a
  normalisation/encoding artifact or a wrong-biennium pull, not a real mass rename → abort.

Per-row eligibility (skip + count): a renamed ``Id`` absent from the live cohort — split
into *hidden* (archived/deleted but still produced; PM 422s evidence on an archived org) vs
*unproduced* (never produced, or owned by another source) — or one PM never anchored (can't
attach by id). Per-row PM rejections and transport blips are isolated; a global auth block
and real bugs propagate.

Thin operator surface — ``python -m usa_wa_sync_powermap.reconcile_committee_names``, no
operator token (shell access is the trust boundary, as with the redrive / contact-label /
active-reconcile CLIs); ``--dry-run`` previews the diff. Idempotent: re-emitting a window
PM already holds is a PM no-op, so ``emitted`` counts observations accepted this run.

Examples::

    python -m usa_wa_sync_powermap.reconcile_committee_names --dry-run
    python -m usa_wa_sync_powermap.reconcile_committee_names --biennium 2025-26
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_sync_powermap.client import DeliveryBlockedError
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from usa_wa_adapter_legislature.refresh import (
    biennium_for_date,
    biennium_start_date,
    previous_biennium,
)
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_sync_powermap.committee_name_reconcile import (
    DEFAULT_MAX_RENAME_FRACTION,
    EXIT_ABORTED,
    reconcile_names_from_maps,
)
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

#: Local ``org_type`` of the rows this diff governs.
_ORG_TYPE = "committee"
#: Low-overlap floor: abort if the two rosters share fewer than this fraction of the
#: **smaller** roster's ``Id``s. WSL committee ``Id``s are **stable** across bienniums, so a
#: healthy current-vs-prior diff overlaps near-totally; a thin overlap means a wrong
#: ``--biennium``, a prior the source lacks data for, or an Id-scheme change — a meaningless
#: diff that would otherwise pass both other guards (``renamed=0`` can't trip the storm
#: floor) and read as a clean "no renames". The smaller-roster denominator reads 1.0 under
#: one-sided growth or drop, so only genuine divergence trips it. Half is generous headroom.
#: Operator-overridable (``--min-overlap-fraction``). (The Joint/`Other` meeting-derived
#: sibling #56 relaxes this — dormancy-prone cohorts overlap thinly by nature.)
DEFAULT_MIN_OVERLAP_FRACTION = 0.5


def _roster_by_id(roster: list[dict], *, label: str) -> dict[str, str]:
    """Map a ``GetCommittees`` roster to ``{source_id: LongName}``, dropping rows missing
    either field (a malformed row can't seed a rename diff).

    A dropped row is logged (``label`` identifies which roster) — a missing ``LongName``
    silently suppresses that committee's rename detection, so it must not pass unobserved."""
    by_id: dict[str, str] = {}
    for committee in roster:
        cid = committee.get("Id")
        long_name = committee.get("LongName")
        if cid is None or not long_name:
            logger.warning(
                "reconcile_names_roster_row_dropped",
                extra={"roster": label, "committee_id": cid, "has_long_name": bool(long_name)},
            )
            continue
        by_id[str(cid)] = long_name
    return by_id


async def reconcile_committee_names(
    session: AsyncSession,
    descriptor: EntityDescriptor,
    wsl_client: Any,
    pm_client: Any,
    *,
    biennium: str,
    dry_run: bool = False,
    max_rename_fraction: float = DEFAULT_MAX_RENAME_FRACTION,
    min_overlap_fraction: float = DEFAULT_MIN_OVERLAP_FRACTION,
) -> dict:
    """Diff WSL's ``GetCommittees`` rosters for ``biennium`` and its predecessor on the
    stable ``Id`` and emit windowed dated-name evidence for each committee whose
    (normalized) ``LongName`` changed.

    Builds the current/prior ``{Id: LongName}`` maps from the rosters (the names diffed
    *and* emitted are WSL's raw ``LongName`` — never the PM-resolved ``Organization.name``
    scalar) and hands them to the shared
    :func:`~usa_wa_sync_powermap.committee_name_reconcile.reconcile_names_from_maps` spine,
    governing the ``org_type='committee'`` class. The Joint/`Other` meeting-derived sibling
    (#56) shares that spine with a different source + relaxed overlap guard.

    Guardrails, per-row eligibility, and emit-to-PM-only semantics: see the spine. ``dry_run``
    still fetches both rosters (so the diff/guards run) but posts nothing.
    """
    prior_label = previous_biennium(biennium)
    current = _roster_by_id(await wsl_client.get_committees(biennium), label=biennium)
    prior = _roster_by_id(await wsl_client.get_committees(prior_label), label=prior_label)
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
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.reconcile_committee_names",
        description=(
            "Detect WSL committee renames across a biennium boundary (stable Id, changed "
            "LongName) and emit windowed dated-name evidence to PM (#46)."
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
            "Abort if more than this fraction of the overlapping cohort shows a changed "
            f"name (default {DEFAULT_MAX_RENAME_FRACTION}); raise it for a genuine "
            "high-churn biennium."
        ),
    )
    parser.add_argument(
        "--min-overlap-fraction",
        type=float,
        default=DEFAULT_MIN_OVERLAP_FRACTION,
        help=(
            "Abort if the two rosters share fewer than this fraction of the current cohort's "
            f"Ids (default {DEFAULT_MIN_OVERLAP_FRACTION}); a thin overlap means a "
            "wrong-biennium pull. Lower it only if a biennium genuinely added many committees."
        ),
    )
    return parser


def _resolve_biennium(arg: str | None) -> str:
    if arg:
        return arg
    return os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())


async def _run(args: argparse.Namespace) -> dict:
    """Open a session + WSL/PM clients, run the reconciliation, and return the summary.

    A ``dry_run`` still needs the WSL client (to fetch both rosters) but no PM client (it
    posts nothing). Emit-to-PM-only, so no commit."""
    biennium = _resolve_biennium(args.biennium)
    settings = get_sidecar_settings()
    factory = get_session_factory()
    wsl_client = WSLClient("CommitteeService")
    if args.dry_run:
        async with factory() as session:
            return await reconcile_committee_names(
                session,
                OrganizationDescriptor(),
                wsl_client,
                None,
                biennium=biennium,
                dry_run=True,
                max_rename_fraction=args.max_rename_fraction,
                min_overlap_fraction=args.min_overlap_fraction,
            )
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to submit observations.")
    pm_client = build_pm_client(settings)
    try:
        async with factory() as session:
            return await reconcile_committee_names(
                session,
                OrganizationDescriptor(),
                wsl_client,
                pm_client,
                biennium=biennium,
                max_rename_fraction=args.max_rename_fraction,
                min_overlap_fraction=args.min_overlap_fraction,
            )
    finally:
        await pm_client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the reconciliation, and print the summary as JSON.

    Exit codes: ``0`` clean (or dry-run); :data:`EXIT_ABORTED` (3) a guardrail abort
    (empty pull / rename storm — took no action); ``1`` ran but some rows rejected/failed;
    ``2`` a global auth block (``DeliveryBlockedError``)."""
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
