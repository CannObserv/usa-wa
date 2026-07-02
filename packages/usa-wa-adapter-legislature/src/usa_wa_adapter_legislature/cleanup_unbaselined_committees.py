"""One-off provenance cleanup: delete the pre-#54 unbaselined committee fetch events
(#64, sub-project 1).

The Jun 19–28 ``committees:2025-26`` daily pulls predate the #54 content-hash baseline
— NULL ``content_hash``, no ``RawPayload`` body (nothing to hash retroactively). They
are superseded by the Jun 30+ archived+hashed pulls of the same resource. This CLI
removes them, first **re-pointing** their ``Citation`` rows to a surviving baselined
fetch event for the same ``resource_id`` — required because ``citations.fetch_event_id``
is ``ondelete=RESTRICT`` (the #54 append-only ledger), so a bare DELETE would error and
a cascade would silently drop provenance.

**Owner role only.** The #54 grants REVOKE ``UPDATE``/``DELETE`` on ``fetch_events`` and
``citations`` from the app role, so this runs under ``DATABASE_URL_OWNER`` (the same DSN
the migrate unit uses) — the app-role serving DSN physically cannot perform it.

Guardrails (fail closed, never orphan or destroy bytes):

* **no survivor** — if no baselined event exists for the resource, abort rather than
  re-point citations to nothing.
* **target has payload** — a NULL-hash event that carries a ``RawPayload`` is a
  contradiction (the runner always hashes what it archives); abort rather than cascade
  archived bytes away.

Idempotent: once the targets are gone a re-run finds nothing (``status=noop``).

    python -m usa_wa_adapter_legislature.cleanup_unbaselined_committees --dry-run
    python -m usa_wa_adapter_legislature.cleanup_unbaselined_committees
"""

import argparse
import asyncio
import json
import os
import sys

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import Citation, FetchEvent, RawPayload

logger = get_logger(__name__)

#: The pre-baseline resource whose NULL-hash events this removes.
DEFAULT_RESOURCE_ID = "committees:2025-26"


async def cleanup_unbaselined(session: AsyncSession, *, resource_id: str) -> dict:
    """Re-point then delete the NULL-``content_hash`` fetch events for ``resource_id``.

    Returns a JSON-able summary. Executes DML in the caller's transaction but does
    **not** commit — the caller decides (dry-run rolls back). See module docstring for
    the abort conditions.
    """
    target_ids = list(
        (
            await session.execute(
                select(FetchEvent.id).where(
                    FetchEvent.resource_id == resource_id,
                    FetchEvent.content_hash.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not target_ids:
        return {"status": "noop", "deleted": 0, "repointed": 0, "survivor": None, "aborted": None}

    # Defensive: an unbaselined event must not carry archived bytes (contradiction).
    payload_on_target = (
        await session.execute(
            select(func.count())
            .select_from(RawPayload)
            .where(RawPayload.fetch_event_id.in_(target_ids))
        )
    ).scalar_one()
    if payload_on_target:
        logger.warning("cleanup_unbaselined_target_has_payload", extra={"resource_id": resource_id})
        return {
            "status": "aborted",
            "aborted": "target_has_payload",
            "deleted": 0,
            "repointed": 0,
            "survivor": None,
        }

    # Survivor: newest baselined event for the same resource — a permanent ledger row
    # this cleanup will not touch, safe to re-anchor the citations onto.
    survivor_id = (
        await session.execute(
            select(FetchEvent.id)
            .where(
                FetchEvent.resource_id == resource_id,
                FetchEvent.content_hash.is_not(None),
            )
            .order_by(FetchEvent.fetched_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if survivor_id is None:
        logger.warning("cleanup_unbaselined_no_survivor", extra={"resource_id": resource_id})
        return {
            "status": "aborted",
            "aborted": "no_survivor",
            "deleted": 0,
            "repointed": 0,
            "survivor": None,
        }

    repointed = (
        await session.execute(
            update(Citation)
            .where(Citation.fetch_event_id.in_(target_ids))
            .values(fetch_event_id=survivor_id)
        )
    ).rowcount
    await session.execute(delete(FetchEvent).where(FetchEvent.id.in_(target_ids)))
    logger.info(
        "cleanup_unbaselined_done",
        extra={
            "resource_id": resource_id,
            "deleted": len(target_ids),
            "repointed": repointed,
            "survivor": str(survivor_id),
        },
    )
    return {
        "status": "cleaned",
        "aborted": None,
        "deleted": len(target_ids),
        "repointed": repointed,
        "survivor": str(survivor_id),
        "targets": [str(t) for t in target_ids],
    }


def _owner_url() -> str:
    url = os.environ.get("DATABASE_URL_OWNER")
    if not url:
        raise RuntimeError(
            "DATABASE_URL_OWNER is required — the app role is REVOKED UPDATE/DELETE on "
            "the provenance ledger (#54); this cleanup must run under the owner role."
        )
    return url


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_adapter_legislature.cleanup_unbaselined_committees",
        description="Delete pre-#54 unbaselined committee fetch events (owner role).",
    )
    parser.add_argument(
        "--resource-id", default=DEFAULT_RESOURCE_ID, help="fetch-event resource_id to clean"
    )
    parser.add_argument("--dry-run", action="store_true", help="preview counts without committing")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    engine = create_async_engine(_owner_url())
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            result = await cleanup_unbaselined(session, resource_id=args.resource_id)
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
    return 3 if result.get("aborted") else 0


if __name__ == "__main__":
    sys.exit(main())
