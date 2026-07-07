"""One-shot subscription bootstrap: ``python -m usa_wa_sync_powermap.bootstrap``.

Populates the PM subscription set + the local cache for the WA subtree once, before
the sidecar starts (PM #203 / usa-wa#10): discovers the subtree, registers every
entity, and backfills current state by id. Idempotent — safe to re-run (a second run
discovers the same set, finds it all subscribed, and does nothing).

Run order at cutover: grant the key ``subscriptions:write`` → reset the
``changes_feed`` cursor → run this bootstrap → start the sidecar service. Failures
propagate (non-zero exit, nothing committed) so a bad bootstrap is loud.
"""

import asyncio

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.registry import build_descriptors, build_reconciler

logger = get_logger(__name__)


async def _amain() -> None:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required for the PM bootstrap.")

    descriptors = build_descriptors(settings)
    client = GeneratedPowerMapClient(settings.powermap_base_url, settings.powermap_api_key)
    engine = SyncEngine(descriptors, client)
    reconciler = build_reconciler(client, engine, settings)
    factory = get_session_factory()
    try:
        async with factory() as session:
            report = await reconciler.sync_subscriptions(session)
            await session.commit()
        logger.info(
            "bootstrap_complete",
            extra={
                "discovered": report.discovered,
                "newly_subscribed": report.newly_subscribed,
                "backfilled": report.backfilled,
                "backfill_skipped": report.backfill_skipped,
                "not_found": report.not_found,
                "skipped_unknown_type": report.skipped_unknown_type,
            },
        )
    finally:
        await client.aclose()


def main() -> None:
    configure_logging()
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
