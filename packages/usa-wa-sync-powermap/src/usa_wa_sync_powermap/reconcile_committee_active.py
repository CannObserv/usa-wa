"""Producer-side reconciliation of a WSL committee's PM ``active`` flag against the
current biennium's roster (#44).

A WSL committee present in one biennium and **absent** from the next is dissolved for
that biennium; one that **reappears** is revived. PM models that as
``Organization.active`` — the operationally-live-vs-dissolved domain flag (#43),
distinct from the reversible ``archived_at`` hide gate. This module is the
**deliberate producer action** that drives it in both directions:

- **Retire** (``active=False``) committees the ``CommitteeService.GetCommittees(biennium)``
  roster no longer lists.
- **Reactivate** (``active=True``) committees that were ``active=False`` locally but
  reappear in the roster.

It is *not* routine sync. The org descriptor's :meth:`to_observation` deliberately
keeps ``active`` out (re-asserting a PM-authoritative field every cycle invites an
LWW write-back fight, #43); this one-shot CLI emits ``active`` only on an observed
transition. PM stays authority for the axis and mirrors the value back read-side, so
the CLI **does not** touch the local ``active`` column.

The narrowing that makes autonomous retirement safe (resolved in the spec's Open Q 5):

- **Explicit-membership source.** Diffs the produced cohort against an explicit
  ``GetCommittees(biennium)`` pull, so "absent from biennium N" is a deliberate diff,
  not an artifact of when the daily refresh ran. (The cohort is House/Senate *standing*
  committees only; statutory joint bodies — the dormant-vs-abolished worry — come from
  ``CommitteeMeetingService``, not here, and renames keep a stable ``Id`` present, so an
  absent ``Id`` is a real dissolution.)
- **Completeness guard.** An empty roster reads as a failed pull, never a mass
  dissolution: an empty pull aborts outright, and a suspiciously-large absent fraction
  trips the **cohort floor** (``--max-absent-fraction``) and aborts.
- **Reactivation self-heals a partial-pull false positive.** The floor catches *gross*
  partial pulls; a *modest* one (a few committees dropped, under the floor) could still
  falsely retire them. But because reactivation is automatic, the next clean pull —
  which lists those committees again — reactivates them. A transient WSL hiccup costs
  one cycle, not a permanent mis-mark. (Retirement is gated by the guards; reactivation
  rides the same guards, so a suspect pull does nothing at all and the heal waits for a
  trustworthy roster.)
- **Skip archived / deleted.** The cohort is :func:`live_only`, so an archived
  (PM 422s ``active_on_archived_org``) or deleted committee is never a candidate.
- **Live-era scoping (#90).** The historical committee backfill (``harvest_committees``,
  model A — each WSL ``Id`` its own org) added ~152 defunct-era committee orgs, all
  defaulting ``active=true``. Absent from the *current* roster they read as a mass
  retirement and trip the cohort floor every run. So the diff is scoped to the **live
  era** — committees whose ``Id`` appears in the current *or* immediately-prior biennium
  roster (``present_ids ∪ prior_ids``, the prior roster read archive-first). Deep-history
  Ids fall out before the diff (counted ``scoped_out``); a genuine prior-biennium
  retirement (in the prior roster, gone from the current) still fires.

Thin operator surface — ``python -m usa_wa_sync_powermap.reconcile_committee_active``,
no operator token (shell access is the trust boundary, as with the redrive and
contact-label CLIs); ``--dry-run`` previews the diff. Idempotent: re-observing an org
already at the target ``active`` value is a PM no-op — so the ``retired`` /
``reactivated`` counts are "observations accepted this run" (re-emits included until the
sidecar mirrors the value back), not strictly net-new transitions.

Examples::

    python -m usa_wa_sync_powermap.reconcile_committee_active --dry-run
    python -m usa_wa_sync_powermap.reconcile_committee_active --biennium 2025-26
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Source
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.queries import live_only
from clearinghouse_sync_powermap.client import DeliveryBlockedError, PayloadRejectedError
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.engine import TRANSIENT_EXCEPTIONS
from usa_wa_adapter_legislature.committee_roster_cohort import CommitteeRosterCohortProvider
from usa_wa_adapter_legislature.refresh import biennium_for_date
from usa_wa_adapter_legislature.synthesis import previous_biennium
from usa_wa_adapter_legislature.transport import WSLClient
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

#: Producer source for WSL committees — scopes the cohort so a future committee-bearing
#: source isn't swept into the reconciliation diff silently.
_SOURCE = "usa_wa_legislature"
#: Local ``org_type`` of the rows this diff governs.
_ORG_TYPE = "committee"
#: Cohort-floor default: abort retirement if more than this fraction of the active local
#: cohort is absent from the roster. Real biennium turnover is a handful of ~34 committees
#: (≲15%); a third leaves headroom while still catching a half-missing partial pull.
#: Operator-overridable (``--max-absent-fraction``) for a genuine high-turnover biennium.
DEFAULT_MAX_ABSENT_FRACTION = 0.34
#: Per-row delivery failures isolated so one bad row doesn't abort the run (transport
#: blips retry next run). ``DeliveryBlockedError`` (401/403) is deliberately **not**
#: here — a global credential failure aborts fast rather than failing every row.
_DELIVERY_FAILURES = TRANSIENT_EXCEPTIONS
#: Exit code for a guardrail abort (empty pull / cohort floor) — distinct from a partial
#: row failure (1) so an operator/cron can tell "took no action" from "acted, some failed".
EXIT_ABORTED = 3


class RosterProvider(Protocol):
    """Biennium → raw committee records — the era-scoping input (#90).

    Returns the *undigested* roster records (``Id``/``Name``/``Agency``/``LongName``) so
    the prior-biennium Id set is built symmetrically with ``present_ids`` — from raw
    ``Id``s, not a name-filtered cohort (which drops blank-``LongName`` committees).
    Satisfied by :class:`CommitteeRosterCohortProvider` (archive-first, so a closed prior
    biennium is a cache hit, not a WSL re-pull)."""

    async def roster_records(self, biennium: str) -> list[dict[str, Any]]: ...


async def _produced_committee_cohort(session: AsyncSession) -> list[Organization]:
    """The produced committees the diff governs: live (not archived / deleted), in both
    ``active`` states (active ones can retire; inactive ones can reactivate)."""
    return list(
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


async def _emit_active(
    descriptor: EntityDescriptor,
    pm_client: Any,
    row: Any,
    *,
    active: bool,
    summary: dict,
    success_key: str,
) -> None:
    """Emit one active-flag observation for ``row``, tallying into ``summary``.

    Skips + counts an unanchored row (can't reattach by id). Isolates a per-row PM
    rejection (422) and transport blip; a global auth block and real bugs propagate.
    On success increments ``success_key`` (``retired`` or ``reactivated``)."""
    if descriptor.anchor_value(row) is None:
        summary["skipped_unanchored"] += 1
        logger.warning("reconcile_active_unanchored", extra={"source_id": row.source_id})
        return
    payload = descriptor.to_active_observation(row, active=active)
    try:
        result = await pm_client.post_observation(descriptor.observe_path, payload)
    except PayloadRejectedError as exc:
        summary["rejected"] += 1
        logger.warning(
            "reconcile_active_rejected", extra={"source_id": row.source_id, "error": str(exc)}
        )
        return
    except _DELIVERY_FAILURES as exc:
        summary["failed"] += 1
        logger.warning(
            "reconcile_active_failed", extra={"source_id": row.source_id, "error": repr(exc)}
        )
        return
    if result.anchored:
        summary[success_key] += 1
    elif result.rejected:
        summary["rejected"] += 1
    else:
        summary["failed"] += 1
    logger.info(
        "reconcile_active_submitted",
        extra={"source_id": row.source_id, "active": active, "disposition": result.disposition},
    )


async def reconcile_committee_active(
    session: AsyncSession,
    descriptor: EntityDescriptor,
    wsl_client: Any,
    pm_client: Any,
    *,
    biennium: str,
    dry_run: bool = False,
    max_absent_fraction: float = DEFAULT_MAX_ABSENT_FRACTION,
    roster_provider: RosterProvider | None = None,
) -> dict:
    """Diff the produced committee cohort against ``GetCommittees(biennium)`` and emit
    ``active=false`` for committees the roster dropped + ``active=true`` for ones it
    lists again.

    **Era scoping (#90).** When a ``roster_provider`` is supplied the diff is restricted
    to the **live era** — committees whose ``source_id`` appears in the current *or*
    immediately-prior biennium roster (``present_ids ∪ prior_ids``). The historical
    committee backfill (``harvest_committees``, model A — each WSL ``Id`` its own org)
    floods the produced cohort with defunct-era Ids, all defaulting ``active=true``;
    absent from the current roster they would read as a mass retirement and trip the
    cohort floor every run (#90). Scoping drops them before the diff (counted
    ``scoped_out``) while keeping a genuine prior-biennium retirement (an ``Id`` in the
    prior roster but gone from the current one). Without a provider the whole produced
    cohort is diffed (legacy behavior).

    **Retirement window is one biennium.** A committee is retired the biennium it first
    goes absent (it is still in the *prior* roster then). If this reconcile does not run
    for a whole biennium, a committee that vanished falls out of *both* the current and
    prior rosters and is scoped out — left ``active=true`` until a bulk one-shot corrects
    it. At the weekly cadence this gap never opens; a multi-biennium reconcile outage is
    the only way to strand a stale-active row.

    Guardrails (see module docstring): an empty roster aborts (``empty_pull``); an
    absent fraction over ``max_absent_fraction`` aborts (``cohort_floor``). Both gate
    the whole run, so a suspect pull does nothing and the next clean pull self-heals
    any modest-partial-pull false retirement via reactivation. Anchored candidates are
    emitted; unanchored ones are counted and skipped. Per-row transport blips and PM
    rejections are isolated; a global auth block and real bugs propagate. ``dry_run``
    runs the diff and guards but posts nothing.

    PM is authority for ``active`` and mirrors it back, so no local column is mutated
    and the caller need not commit. Returns a JSON-able summary.

    ``retired``/``reactivated`` count **observations accepted this run**, not net-new
    state changes: because the CLI is emit-only, a still-absent committee is re-emitted
    each run (a PM no-op) until the sidecar mirrors ``active`` back onto the local row
    and it drops out of the next diff. So a non-zero count on a repeat run can be
    idempotent re-emits, not fresh transitions.
    """
    roster = await wsl_client.get_committees(biennium)
    present_ids = {str(c["Id"]) for c in roster if c.get("Id") is not None}
    full_cohort = await _produced_committee_cohort(session)
    if roster_provider is not None:
        prior = previous_biennium(biennium)
        prior_records = await roster_provider.roster_records(prior)
        prior_ids = {str(r["Id"]) for r in prior_records if r.get("Id") is not None}
        era_ids = present_ids | prior_ids
        cohort = [c for c in full_cohort if c.source_id in era_ids]
    else:
        cohort = full_cohort
    scoped_out = len(full_cohort) - len(cohort)
    active_cohort = [c for c in cohort if c.active]
    to_retire = [c for c in active_cohort if c.source_id not in present_ids]
    to_reactivate = [c for c in cohort if not c.active and c.source_id in present_ids]
    summary = {
        "biennium": biennium,
        "present": len(present_ids),
        "cohort": len(cohort),
        "scoped_out": scoped_out,
        "absent": len(to_retire),
        "returning": len(to_reactivate),
        "retired": 0,
        "reactivated": 0,
        "skipped_unanchored": 0,
        "rejected": 0,
        "failed": 0,
        "dry_run": dry_run,
        "aborted": None,
    }
    if not present_ids:
        # A failed/empty pull must never read as "every committee was abolished".
        summary["aborted"] = "empty_pull"
        logger.warning(
            "reconcile_active_aborted", extra={"reason": "empty_pull", "biennium": biennium}
        )
        return summary
    if active_cohort and len(to_retire) / len(active_cohort) > max_absent_fraction:
        # Mass absence ⇒ suspect partial pull, not a real mass dissolution.
        summary["aborted"] = "cohort_floor"
        logger.warning(
            "reconcile_active_aborted",
            extra={
                "reason": "cohort_floor",
                "absent": len(to_retire),
                "active_cohort": len(active_cohort),
                "max_absent_fraction": max_absent_fraction,
            },
        )
        return summary
    if dry_run:
        return summary
    for row in to_retire:
        await _emit_active(
            descriptor, pm_client, row, active=False, summary=summary, success_key="retired"
        )
    for row in to_reactivate:
        await _emit_active(
            descriptor, pm_client, row, active=True, summary=summary, success_key="reactivated"
        )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.reconcile_committee_active",
        description=(
            "Reconcile PM Organization.active for WSL committees against the current "
            "biennium's GetCommittees roster: retire the absent, reactivate the returning (#44)."
        ),
    )
    parser.add_argument(
        "--biennium",
        default=None,
        help="Biennium label (e.g. 2025-26). Defaults to USA_WA_BIENNIUM or the current date.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the diff and guardrails without posting any observation.",
    )
    parser.add_argument(
        "--max-absent-fraction",
        type=float,
        default=DEFAULT_MAX_ABSENT_FRACTION,
        help=(
            "Abort retirement if more than this fraction of the active cohort is absent "
            f"(default {DEFAULT_MAX_ABSENT_FRACTION}); raise it for a genuine "
            "high-turnover biennium."
        ),
    )
    return parser


def _resolve_biennium(arg: str | None) -> str:
    if arg:
        return arg
    return os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(datetime.now(UTC).date())


async def _build_roster_provider(
    session: AsyncSession, wsl_client: Any
) -> CommitteeRosterCohortProvider:
    """The archive-first prior-roster provider for era scoping (#90).

    Bound to the WSL provenance source (``source_id``) so a closed prior biennium is a
    cache hit on the ``committees-roster:<biennium>`` archive (written by
    ``harvest_committees``), not a fresh ``GetCommittees`` pull.

    A **read-only** lookup, not get-or-create: this reconcile never commits (PM is
    authority for ``active`` and mirrors it back), so it has no business provisioning the
    Source. If the Source is somehow absent (a DB that never ran a WSL pull), the provider
    gets ``source_id=None`` and simply live-pulls every biennium — no archive to read."""
    # _SOURCE is the Organization.source producer string; it equals the provenance
    # Source.slug by convention, so it doubles as the Source lookup key here.
    source = (
        await session.execute(select(Source).where(Source.slug == _SOURCE))
    ).scalar_one_or_none()
    source_id = source.id if source is not None else None
    return CommitteeRosterCohortProvider(wsl_client, session=session, source_id=source_id)


async def _run(args: argparse.Namespace) -> dict:
    """Open a session + WSL/PM clients, run the reconciliation, and return the summary.

    A ``dry_run`` still needs the WSL client (to fetch the roster) but no PM client
    (it posts nothing). The local ``active`` column is PM-mirrored, so no commit."""
    biennium = _resolve_biennium(args.biennium)
    settings = get_sidecar_settings()
    factory = get_session_factory()
    wsl_client = WSLClient("CommitteeService")
    if args.dry_run:
        async with factory() as session:
            return await reconcile_committee_active(
                session,
                OrganizationDescriptor(),
                wsl_client,
                None,
                biennium=biennium,
                dry_run=True,
                max_absent_fraction=args.max_absent_fraction,
                roster_provider=await _build_roster_provider(session, wsl_client),
            )
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to submit observations.")
    pm_client = build_pm_client(settings)
    try:
        async with factory() as session:
            return await reconcile_committee_active(
                session,
                OrganizationDescriptor(),
                wsl_client,
                pm_client,
                biennium=biennium,
                max_absent_fraction=args.max_absent_fraction,
                roster_provider=await _build_roster_provider(session, wsl_client),
            )
    finally:
        await pm_client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the reconciliation, and print the summary as JSON.

    Exit codes: ``0`` clean (or dry-run); :data:`EXIT_ABORTED` (3) a guardrail abort
    (empty pull / cohort floor — took no action); ``1`` ran but some rows
    rejected/failed; ``2`` a global auth block (``DeliveryBlockedError``)."""
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
