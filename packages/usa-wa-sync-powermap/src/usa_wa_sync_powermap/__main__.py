"""Sidecar entrypoint: ``python -m usa_wa_sync_powermap``.

Configures logging once, wires the engine + generated PM client + descriptor
registry, and runs the daemon until killed.
"""

import asyncio

from clearinghouse_core.database import get_session_factory, log_connection_fingerprint
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from usa_wa_sync_powermap.alerts import build_alert
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.registry import build_descriptors, build_reconciler
from usa_wa_sync_powermap.role_type_catalog import sync_role_type_catalog
from usa_wa_sync_powermap.sidecar import Sidecar

logger = get_logger(__name__)


async def _amain() -> None:
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required for the PM sidecar.")

    session_factory = get_session_factory()
    async with session_factory() as session:
        await log_connection_fingerprint(session, context="sync-sidecar")

    descriptors = build_descriptors(settings)
    client = GeneratedPowerMapClient(settings.powermap_base_url, settings.powermap_api_key)
    engine = SyncEngine(descriptors, client)
    reconciler = build_reconciler(client, engine, settings)
    # Failure-streak alerting (#85): fail-closed like notify-failure.sh — no
    # recipient disables the email path, loudly, rather than silently dropping it.
    alert = build_alert(settings.usa_wa_alert_email)
    if alert is None:
        logger.warning(
            "sidecar_alerting_disabled",
            extra={"reason": "USA_WA_ALERT_EMAIL unset — failure streaks will not email"},
        )
    sidecar = Sidecar(
        engine,
        descriptors,
        session_factory,
        feed_poll_seconds=settings.feed_poll_seconds,
        reconciler=reconciler,
        subscription_backstop_cadence=settings.subscription_backstop_cadence,
        outbox_commit_chunk_size=settings.outbox_commit_chunk_size,
        catalog_sync=lambda session: sync_role_type_catalog(session, client),
        alert=alert,
        failure_alert_threshold=settings.failure_alert_threshold,
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
