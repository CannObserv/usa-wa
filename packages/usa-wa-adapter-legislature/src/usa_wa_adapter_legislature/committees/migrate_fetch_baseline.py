"""One-off provenance repair: retroactively baseline the pre-#54 committee fetch
events (#64, sub-project 1).

The Jun 19–28 ``committees:2025-26`` daily pulls predate the #54 content-hash baseline
(NULL ``content_hash``) — but they DID archive their bodies (each has a ``RawPayload``).
So rather than delete them, this backfills ``content_hash = sha256(RawPayload.body)`` —
exactly the digest the runner now derives (see ``AdapterRunner._record_fetch_event``) —
converting them from "unbaselined" to integrity-verified while keeping the fetch history
**and** the archived bytes. Closes the sweep's ``unbaselined`` count for the resource.

**Owner role only.** The #54 grants REVOKE ``UPDATE`` on ``fetch_events`` from the app
role, so this runs under ``DATABASE_URL_OWNER`` (the migrate DSN) — the app-role serving
DSN physically cannot rewrite the ledger.

A NULL-hash event with **no** payload can't be hashed (nothing to baseline); it is
counted (``skipped_no_payload``) and left alone — never treated as verified. Idempotent:
once an event carries a hash it's no longer selected (``status=noop`` when none remain).

    python -m usa_wa_adapter_legislature.baseline_unbaselined_committees --dry-run
    python -m usa_wa_adapter_legislature.baseline_unbaselined_committees
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import FetchEvent, RawPayload

logger = get_logger(__name__)

#: The pre-baseline resource whose NULL-hash events this repairs.
DEFAULT_RESOURCE_ID = "committees:2025-26"


async def baseline_unbaselined(session: AsyncSession, *, resource_id: str) -> dict:
    """Backfill ``content_hash = sha256(body)`` for NULL-hash events of ``resource_id``.

    Returns a JSON-able summary. Executes UPDATEs in the caller's transaction but does
    **not** commit — the caller decides (dry-run rolls back). Payload-less NULL-hash
    events are skipped and counted.
    """
    rows = (
        await session.execute(
            select(FetchEvent.id, RawPayload.body)
            .outerjoin(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
            .where(
                FetchEvent.resource_id == resource_id,
                FetchEvent.content_hash.is_(None),
            )
        )
    ).all()
    if not rows:
        return {"status": "noop", "baselined": 0, "skipped_no_payload": 0}

    baselined = 0
    skipped = 0
    for fetch_event_id, body in rows:
        if body is None:
            skipped += 1
            logger.warning(
                "baseline_skip_no_payload",
                extra={"resource_id": resource_id, "fetch_event_id": str(fetch_event_id)},
            )
            continue
        digest = hashlib.sha256(body).digest()
        event = await session.get(FetchEvent, fetch_event_id)
        event.content_hash = digest
        baselined += 1
    await session.flush()
    logger.info(
        "baseline_unbaselined_done",
        extra={"resource_id": resource_id, "baselined": baselined, "skipped_no_payload": skipped},
    )
    return {
        "status": "baselined" if baselined else "skipped",
        "baselined": baselined,
        "skipped_no_payload": skipped,
    }


def _owner_url() -> str:
    url = os.environ.get("DATABASE_URL_OWNER")
    if not url:
        raise RuntimeError(
            "DATABASE_URL_OWNER is required — the app role is REVOKED UPDATE on the "
            "provenance ledger (#54); baselining must run under the owner role."
        )
    return url


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_adapter_legislature.baseline_unbaselined_committees",
        description="Retroactively baseline pre-#54 committee fetch events (owner role).",
    )
    parser.add_argument(
        "--resource-id", default=DEFAULT_RESOURCE_ID, help="fetch-event resource_id to baseline"
    )
    parser.add_argument("--dry-run", action="store_true", help="preview counts without committing")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    engine = create_async_engine(_owner_url())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            result = await baseline_unbaselined(session, resource_id=args.resource_id)
            if args.dry_run:
                await session.rollback()
                result = {**result, "dry_run": True}
            else:
                await session.commit()
            return result
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
