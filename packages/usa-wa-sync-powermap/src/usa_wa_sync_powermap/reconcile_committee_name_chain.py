"""Full committee rename-chain emission (sub-project 3, Phase B).

The deep-history counterpart of :mod:`reconcile_committee_names` (#46): instead of one
current-vs-prior hop, it emits the **whole** rename chain. Reads every archived
``committees-roster:<biennium>`` roster offline via
:class:`~usa_wa_adapter_legislature.committees.cohort.CommitteeRosterCohortProvider`
(archive-first — no WSL re-pull), builds each stable id's full
``normalize_name(LongName)`` timeline via
:func:`~usa_wa_sync_powermap.committee_name_chain.build_rename_chain`, and emits windowed
``former``/``legal`` dated-name evidence for each transition through the #46/#56 spine's
per-row emit (:func:`~usa_wa_sync_powermap.committee_name_reconcile._emit_names`).

Emit-to-PM-only: PM curates ``is_canonical`` and the #45 read mirror brings the windows
back (which now stick, given the #65 fill-only refresh). No local write. A transition on
an id absent from the live cohort is counted-skipped (*hidden* = archived/deleted but
produced vs *unproduced*). Empty archive aborts. No operator token (shell = trust
boundary). ``--dry-run`` previews; exit ``0`` clean · ``1`` some rejected/failed · ``2``
auth block · ``3`` empty-archive abort.

    python -m usa_wa_sync_powermap.reconcile_committee_name_chain --dry-run
    python -m usa_wa_sync_powermap.reconcile_committee_name_chain
"""

from __future__ import annotations

import argparse
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.cohorts import committee_roster_provider
from usa_wa_adapter_legislature.provisioning import get_or_create_source
from usa_wa_common.jurisdiction import resolve_jurisdiction
from usa_wa_sync_powermap.committee_name_chain import (
    DEFAULT_MAX_RENAME_FRACTION,
    DEFAULT_STORM_FLOOR,
    build_rename_chain,
)
from usa_wa_sync_powermap.committee_name_reconcile import (
    _emit_names,
    live_cohort_by_source_id,
    produced_source_ids,
)
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor
from usa_wa_sync_powermap.jobs import run_pm_job
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "pm-committee-name-chain-reconcile"

_ORG_TYPE = "committee"


async def emit_rename_chain(
    session: AsyncSession,
    descriptor: Any,
    pm_client: Any,
    provider: Any,
    *,
    bienniums: list[str] | None = None,
    dry_run: bool = False,
    max_rename_fraction: float = DEFAULT_MAX_RENAME_FRACTION,
    storm_floor: int = DEFAULT_STORM_FLOOR,
) -> dict:
    """Build the full chain over the archived rosters and emit each transition.

    ``bienniums`` defaults to every archived roster (the chain's domain). Empty archive
    aborts. Emit-only; the caller need not commit."""
    bienniums = bienniums or await provider.archived_bienniums()
    summary = {
        "bienniums": len(bienniums),
        "transitions": 0,
        "emitted": 0,
        "skipped_unanchored": 0,
        "skipped_hidden": 0,
        "skipped_unproduced": 0,
        "rejected": 0,
        "failed": 0,
        "storm_skipped": [],
        "dry_run": dry_run,
        "aborted": None,
    }
    if not bienniums:
        summary["aborted"] = "empty_archive"
        return summary

    cohorts = {biennium: await provider.cohort(biennium) for biennium in bienniums}
    chain = build_rename_chain(
        cohorts, max_rename_fraction=max_rename_fraction, storm_floor=storm_floor
    )
    summary["transitions"] = len(chain["transitions"])
    summary["storm_skipped"] = chain["storm_skipped"]

    live = await live_cohort_by_source_id(session, org_type=_ORG_TYPE)
    produced = await produced_source_ids(session, org_type=_ORG_TYPE)

    for transition in chain["transitions"]:
        row = live.get(transition.source_id)
        if row is None:
            key = "skipped_hidden" if transition.source_id in produced else "skipped_unproduced"
            summary[key] += 1
            logger.info("chain_absent", extra={"source_id": transition.source_id, "class": key})
            continue
        if dry_run:
            continue
        await _emit_names(
            descriptor,
            pm_client,
            row,
            prior_name=transition.former_name,
            new_name=transition.legal_name,
            boundary=transition.effective_start,
            summary=summary,
            org_type=_ORG_TYPE,
        )
    return summary


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute this job's own flags to the harness's shared parser."""
    parser.add_argument("--max-rename-fraction", type=float, default=DEFAULT_MAX_RENAME_FRACTION)
    parser.add_argument("--storm-floor", type=int, default=DEFAULT_STORM_FLOOR)


async def _run(args: argparse.Namespace, factory: Any) -> dict:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — cannot emit to Power Map.")
    async with factory() as session:
        jurisdiction = await resolve_jurisdiction(session)
        source = await get_or_create_source(session, jurisdiction)
        # The Source is get-or-created above (this path writes provenance), so it is
        # passed explicitly rather than left to the factory's read-only lookup.
        provider = await committee_roster_provider(session, source_id=source.id)
        pm_client = build_pm_client(settings)
        try:
            return await emit_rename_chain(
                session,
                OrganizationDescriptor(),
                pm_client,
                provider,
                dry_run=args.dry_run,
                max_rename_fraction=args.max_rename_fraction,
                storm_floor=args.storm_floor,
            )
        finally:
            await pm_client.aclose()


async def _chain_job(ctx: JobContext) -> JobResult:
    """Harness handler. ``commit=False`` — ``_run`` get-or-creates the Source but the
    pre-#179b CLI never committed, so that write stays a no-op here too."""
    factory = ctx.require_session_factory()
    return await run_pm_job(lambda: _run(ctx.args, factory))


def main(argv: list[str] | None = None) -> int:
    """Emit the full rename chain.

    Exit codes (unchanged, :mod:`usa_wa_sync_powermap.jobs`): ``0`` clean or dry-run;
    ``1`` some rows rejected/failed; ``2`` a global auth block; ``3`` a guardrail abort.
    """
    return run_job(
        JOB_SLUG,
        _chain_job,
        argv=argv,
        prog="python -m usa_wa_sync_powermap.reconcile_committee_name_chain",
        description="Emit the full committee rename chain from the archived rosters.",
        extra_args=_add_args,
        commit=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
