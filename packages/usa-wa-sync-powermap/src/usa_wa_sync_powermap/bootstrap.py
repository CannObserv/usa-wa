"""One-shot subscription bootstrap: ``python -m usa_wa_sync_powermap.bootstrap``.

Populates the PM subscription set + the local cache for the WA subtree once, before
the sidecar starts (PM #203 / usa-wa#10): discovers the subtree, registers every
entity, and backfills current state by id. Idempotent — safe to re-run (a second run
discovers the same set, finds it all subscribed, and does nothing).

Run order at cutover: grant the key ``subscriptions:write`` → reset the
``changes_feed`` cursor → run this bootstrap → start the sidecar service. Failures
propagate (non-zero exit, nothing committed) so a bad bootstrap is loud.
"""

from clearinghouse_core.job import JobContext, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.engine import SyncEngine
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.registry import build_descriptors, build_pm_client, build_reconciler

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "pm-subscription-bootstrap"


async def _bootstrap_job(ctx: JobContext) -> dict:
    """Harness handler. ``commit=False``: the reconciler's own session commits, exactly as
    the pre-#179b entrypoint did — failures still propagate (nothing committed), so a bad
    bootstrap stays loud."""
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required for the PM bootstrap.")

    descriptors = build_descriptors(settings)
    client = build_pm_client(settings)
    engine = SyncEngine(descriptors, client)
    reconciler = build_reconciler(client, engine, settings)
    factory = ctx.require_session_factory()
    try:
        async with factory() as session:
            report = await reconciler.sync_subscriptions(session)
            await session.commit()
    finally:
        await client.aclose()
    counters = {
        "discovered": report.discovered,
        "newly_subscribed": report.newly_subscribed,
        "backfilled": report.backfilled,
        "backfill_skipped": report.backfill_skipped,
        "not_found": report.not_found,
        "skipped_unknown_type": report.skipped_unknown_type,
    }
    logger.info("bootstrap_complete", extra=counters)
    return counters


def main(argv: list[str] | None = None) -> int:
    """Bootstrap the PM subscription set. Exit ``0`` clean · ``1`` failed · ``2`` config.

    **Changed at #179b**: a failure used to escape as a traceback (exit 1 from the Python
    interpreter); it is now a logged ``failed`` run and the same exit 1, with a ledger row.

    **No ``--dry-run``** (``dry_run=False``, CR #196 finding 47). This registers
    subscriptions on PM over the network and commits the local cache in its own session,
    so the flag could honour neither half of "run the work but roll back". It is
    idempotent instead: a second run finds everything subscribed and does nothing.
    """
    return run_job(
        JOB_SLUG,
        _bootstrap_job,
        argv=argv,
        prog="python -m usa_wa_sync_powermap.bootstrap",
        description="One-shot PM subscription bootstrap (PM #203 / usa-wa#10).",
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
