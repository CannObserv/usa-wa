"""One-shot force-adopt heal for LWW-locked committee curation (#65 Part 2).

Before the fill-only refresh fix (#65), the daily WSL refresh bumped
``Organization.updated_at`` on every re-pull, pushing the local clock ahead of PM.
For committees PM had already curated, the sidecar's LWW then reads local-newer and
**keeps the stale local values** — the row is locked out of PM's curation
(``name``/``acronym``/dated-name windows never adopted). The fill-only fix stops
*future* clock bumps, but the already-locked rows stay locked until their local
clock falls back below PM's — which never happens on its own.

This CLI unsticks them: for the whole anchored produced cohort it re-fetches each
PM ``OrgDetail`` and force-applies it via ``OrganizationDescriptor.upsert_from_pm``
— the PM-wins branch of the sync engine's ``apply_record`` (adopt curated fields +
mirror the embedded name/acronym windows), then stamps clock parity
(``set_last_updated``) — run **unconditionally**, bypassing the LWW check. So a
locked row adopts curation once; a row already at parity re-adopts the same values
(idempotent). After the fill-only refresh deploy, healed rows stay healed.

Local write on ``canonical`` tables only (no provenance) → app role. Read-only
against PM. No operator token (shell = trust boundary, as with the reconcile CLIs).
``--dry-run`` previews; exit ``0`` clean · ``2`` auth · ``3`` empty-cohort abort.

    python -m usa_wa_sync_powermap.heal_committee_curation --dry-run
    python -m usa_wa_sync_powermap.heal_committee_curation
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.queries import live_only
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor
from usa_wa_sync_powermap.jobs import never, run_pm_job
from usa_wa_sync_powermap.registry import build_pm_client

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "pm-committee-curation-heal"

#: Producer source whose anchored cohort this heals.
_SOURCE = "usa_wa_legislature"
#: Exit code for a guardrail abort (empty cohort).
EXIT_ABORTED = 3


async def _anchored_cohort(session: AsyncSession) -> list[Organization]:
    """Live produced orgs already anchored to PM (the heal targets)."""
    return list(
        (
            await session.execute(
                live_only(
                    select(Organization).where(
                        Organization.source == _SOURCE,
                        Organization.pm_organization_id.is_not(None),
                    ),
                    Organization,
                )
            )
        )
        .scalars()
        .all()
    )


async def heal_committee_curation(session: AsyncSession, descriptor: Any, client: Any) -> dict:
    """Force-apply PM's curated record to every anchored produced org.

    The PM-wins branch of ``apply_record`` run unconditionally: fetch each org's PM
    record, ``upsert_from_pm`` (adopt fields + mirror windows), then stamp clock
    parity. Bypasses LWW so an LWW-locked row adopts curation. Empty cohort aborts.
    Executes writes in the caller's transaction; does not commit.
    """
    cohort = await _anchored_cohort(session)
    if not cohort:
        return {"checked": 0, "healed": 0, "skipped_missing_pm": 0, "aborted": "empty_cohort"}

    healed = 0
    skipped_missing = 0
    for row in cohort:
        pm_id = descriptor.anchor_value(row)
        record = await descriptor.fetch_record(client, pm_id)
        if record is None:
            skipped_missing += 1
            logger.warning("heal_pm_missing", extra={"source_id": row.source_id})
            continue
        await descriptor.upsert_from_pm(session, record, existing=row)
        pm_ts = descriptor.last_updated(record)
        if pm_ts is not None:  # clock parity so the next reconcile sees no local-newer
            descriptor.set_last_updated(row, pm_ts)
        healed += 1
        logger.info("heal_adopted", extra={"source_id": row.source_id})

    return {
        "checked": len(cohort),
        "healed": healed,
        "skipped_missing_pm": skipped_missing,
        "aborted": None,
    }


async def _run(dry_run: bool, factory: Any) -> dict:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — cannot read from Power Map.")
    async with factory() as session:
        client = build_pm_client(settings)
        try:
            result = await heal_committee_curation(session, OrganizationDescriptor(), client)
            if dry_run:
                await session.rollback()
                result = {**result, "dry_run": True}
            else:
                await session.commit()
            return result
        finally:
            await client.aclose()


async def _heal_job(ctx: JobContext) -> JobResult:
    """Harness handler. ``commit=False``; ``_run`` keeps its own session/commit."""
    factory = ctx.require_session_factory()
    return await run_pm_job(lambda: _run(ctx.dry_run, factory), failed_when=never)


def main(argv: list[str] | None = None) -> int:
    """Force-adopt PM curation.

    Exit codes (unchanged): ``0`` clean or dry-run; ``2`` a global auth block; ``3`` a
    guardrail abort, ledgered as ``degraded``.
    """
    return run_job(
        JOB_SLUG,
        _heal_job,
        argv=argv,
        prog="python -m usa_wa_sync_powermap.heal_committee_curation",
        description="Force-adopt PM curation for LWW-locked anchored orgs (#65).",
        commit=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
