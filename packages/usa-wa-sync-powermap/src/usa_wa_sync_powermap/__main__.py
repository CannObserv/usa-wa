"""Sidecar entrypoint: ``python -m usa_wa_sync_powermap``.

Configures logging once, wires the engine + generated PM client + descriptor
registry, and runs the daemon until killed.
"""

import asyncio

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from clearinghouse_sync_powermap.subscriptions import SubscriptionReconciler
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.registry import build_descriptors, build_discovery_spec
from usa_wa_sync_powermap.sidecar import Sidecar

logger = get_logger(__name__)


async def _amain() -> None:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required for the PM sidecar.")

    descriptors = build_descriptors()
    client = GeneratedPowerMapClient(settings.powermap_base_url, settings.powermap_api_key)
    engine = SyncEngine(descriptors, client)
    reconciler = SubscriptionReconciler(client, engine, build_discovery_spec(settings))
    sidecar = Sidecar(
        engine,
        descriptors,
        get_session_factory(),
        feed_poll_seconds=settings.feed_poll_seconds,
        reconciler=reconciler,
        subscription_backstop_cadence=settings.subscription_backstop_cadence,
        outbox_commit_chunk_size=settings.outbox_commit_chunk_size,
    )
    try:
        await sidecar.run_forever()
    finally:
        await client.aclose()


def main() -> None:
    configure_logging()
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
