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
- **Rename-storm floor.** A renamed fraction over ``--max-rename-fraction`` reads as a
  normalisation/encoding artifact or a wrong-biennium pull, not a real mass rename → abort.

Per-row eligibility (skip + count): a renamed ``Id`` we never produced (no live row), one
that is archived/deleted (out of the :func:`live_only` cohort, and PM 422s evidence on an
archived org), or one PM never anchored (can't attach by id). Per-row PM rejections and
transport blips are isolated; a global auth block and real bugs propagate.

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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.queries import live_only
from clearinghouse_sync_powermap.client import DeliveryBlockedError, PayloadRejectedError
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, normalize_name
from clearinghouse_sync_powermap.engine import TRANSIENT_EXCEPTIONS
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from usa_wa_adapter_legislature.refresh import (
    biennium_for_date,
    biennium_start_date,
    previous_biennium,
)
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor

logger = get_logger(__name__)

#: Producer source for WSL committees — scopes the cohort so a future committee-bearing
#: source isn't swept into the rename diff silently.
_SOURCE = "usa_wa_legislature"
#: Local ``org_type`` of the rows this diff governs.
_ORG_TYPE = "committee"
#: Rename-storm default: abort if more than this fraction of the overlapping cohort shows a
#: changed name. Real biennium renames are a handful of ~34 committees; a third leaves
#: headroom while still catching a wrong-biennium pull or a normalisation artifact.
#: Operator-overridable (``--max-rename-fraction``).
DEFAULT_MAX_RENAME_FRACTION = 0.34
#: Per-row delivery failures isolated so one bad row doesn't abort the run. As with #44,
#: ``DeliveryBlockedError`` (401/403) is deliberately **not** here — a global credential
#: failure aborts fast rather than failing every row.
_DELIVERY_FAILURES = TRANSIENT_EXCEPTIONS
#: Exit code for a guardrail abort (empty pull / rename storm) — distinct from a partial
#: row failure (1) so an operator/cron can tell "took no action" from "acted, some failed".
EXIT_ABORTED = 3


def _roster_by_id(roster: list[dict]) -> dict[str, str]:
    """Map a ``GetCommittees`` roster to ``{source_id: LongName}``, dropping rows missing
    either field (a malformed row can't seed a rename diff)."""
    by_id: dict[str, str] = {}
    for committee in roster:
        cid = committee.get("Id")
        long_name = committee.get("LongName")
        if cid is None or not long_name:
            continue
        by_id[str(cid)] = long_name
    return by_id


async def _live_committee_by_source_id(session: AsyncSession) -> dict[str, Organization]:
    """The live (not archived / deleted) produced committees, keyed by ``source_id`` (the
    WSL ``Id``) for the rename join."""
    rows = (
        (
            await session.execute(
                live_only(
                    select(Organization).where(
                        Organization.source == _SOURCE,
                        Organization.org_type == _ORG_TYPE,
                    ),
                    Organization,
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.source_id: row for row in rows}


async def _emit_names(
    descriptor: EntityDescriptor,
    pm_client: Any,
    row: Any,
    *,
    prior_name: str,
    new_name: str,
    boundary: Any,
    summary: dict,
) -> None:
    """Emit one dated-name observation for a renamed ``row``, tallying into ``summary``.

    Skips + counts an unanchored row (can't attach by id). Isolates a per-row PM rejection
    (422) and transport blip; a global auth block and real bugs propagate. On success
    increments ``emitted``."""
    if descriptor.anchor_value(row) is None:
        summary["skipped_unanchored"] += 1
        logger.warning("reconcile_names_unanchored", extra={"source_id": row.source_id})
        return
    payload = descriptor.to_names_observation(
        row, prior_name=prior_name, new_name=new_name, boundary=boundary
    )
    try:
        result = await pm_client.post_observation(descriptor.observe_path, payload)
    except PayloadRejectedError as exc:
        summary["rejected"] += 1
        logger.warning(
            "reconcile_names_rejected", extra={"source_id": row.source_id, "error": str(exc)}
        )
        return
    except _DELIVERY_FAILURES as exc:
        summary["failed"] += 1
        logger.warning(
            "reconcile_names_failed", extra={"source_id": row.source_id, "error": repr(exc)}
        )
        return
    if result.anchored:
        summary["emitted"] += 1
    elif result.rejected:
        summary["rejected"] += 1
    else:
        summary["failed"] += 1
    logger.info(
        "reconcile_names_submitted",
        extra={"source_id": row.source_id, "disposition": result.disposition},
    )


async def reconcile_committee_names(
    session: AsyncSession,
    descriptor: EntityDescriptor,
    wsl_client: Any,
    pm_client: Any,
    *,
    biennium: str,
    dry_run: bool = False,
    max_rename_fraction: float = DEFAULT_MAX_RENAME_FRACTION,
) -> dict:
    """Diff WSL's ``GetCommittees`` rosters for ``biennium`` and its predecessor on the
    stable ``Id`` and emit windowed dated-name evidence for each committee whose
    (normalized) ``LongName`` changed.

    Guardrails (see module docstring): either roster empty aborts (``empty_pull``); a
    renamed fraction over ``max_rename_fraction`` aborts (``rename_storm``). Both gate the
    whole run. Renamed committees we never produced (or that are archived/deleted/from
    another source — absent from the live cohort) are counted-skipped; unanchored ones are
    counted and skipped. Per-row transport blips and PM rejections are isolated; a global
    auth block and real bugs propagate. ``dry_run`` runs the diff and guards but posts
    nothing.

    Emit-to-PM-only: PM resolves canonical and the #45 read mirror brings the windows back,
    so no local column is mutated and the caller need not commit. Returns a JSON-able
    summary. ``emitted`` counts observations accepted this run (idempotent re-emits
    included), not strictly net-new renames.
    """
    prior_label = previous_biennium(biennium)
    current = _roster_by_id(await wsl_client.get_committees(biennium))
    prior = _roster_by_id(await wsl_client.get_committees(prior_label))
    overlap = current.keys() & prior.keys()
    renamed = sorted(
        cid for cid in overlap if normalize_name(prior[cid]) != normalize_name(current[cid])
    )
    summary = {
        "biennium": biennium,
        "prior_biennium": prior_label,
        "current": len(current),
        "prior": len(prior),
        "overlap": len(overlap),
        "renamed": len(renamed),
        "emitted": 0,
        "skipped_unanchored": 0,
        "skipped_unproduced": 0,
        "rejected": 0,
        "failed": 0,
        "dry_run": dry_run,
        "aborted": None,
    }
    if not current or not prior:
        # Either side empty ⇒ a failed pull, never a real diff (an empty current windows
        # nothing; an empty prior makes every committee look brand-new and mis-windows).
        summary["aborted"] = "empty_pull"
        logger.warning(
            "reconcile_names_aborted", extra={"reason": "empty_pull", "biennium": biennium}
        )
        return summary
    if overlap and len(renamed) / len(overlap) > max_rename_fraction:
        # Mass rename ⇒ suspect normalisation artifact / wrong-biennium pull.
        summary["aborted"] = "rename_storm"
        logger.warning(
            "reconcile_names_aborted",
            extra={
                "reason": "rename_storm",
                "renamed": len(renamed),
                "overlap": len(overlap),
                "max_rename_fraction": max_rename_fraction,
            },
        )
        return summary
    if dry_run:
        return summary
    boundary = biennium_start_date(biennium)
    cohort = await _live_committee_by_source_id(session)
    for cid in renamed:
        row = cohort.get(cid)
        if row is None:
            # Never produced, or archived/deleted/other-source ⇒ out of the live cohort.
            summary["skipped_unproduced"] += 1
            logger.warning("reconcile_names_unproduced", extra={"source_id": cid})
            continue
        await _emit_names(
            descriptor,
            pm_client,
            row,
            prior_name=prior[cid],
            new_name=current[cid],
            boundary=boundary,
            summary=summary,
        )
    return summary


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
            )
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to submit observations.")
    pm_client = GeneratedPowerMapClient(settings.powermap_base_url, settings.powermap_api_key)
    try:
        async with factory() as session:
            return await reconcile_committee_names(
                session,
                OrganizationDescriptor(),
                wsl_client,
                pm_client,
                biennium=biennium,
                max_rename_fraction=args.max_rename_fraction,
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
