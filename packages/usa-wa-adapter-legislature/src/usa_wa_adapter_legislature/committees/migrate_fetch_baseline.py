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

    python -m usa_wa_adapter_legislature.committees.migrate_fetch_baseline --dry-run
    python -m usa_wa_adapter_legislature.committees.migrate_fetch_baseline
"""

import argparse
import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.config import DATABASE_ROLE_OWNER
from clearinghouse_core.job import JobContext, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, RawPayload

logger = get_logger(__name__)

#: The pre-baseline resource whose NULL-hash events this repairs.
DEFAULT_RESOURCE_ID = "committees:2025-26"

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "wsl-committee-fetch-baseline-migrate"


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


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the migration's own flag to the harness's shared parser."""
    parser.add_argument(
        "--resource-id", default=DEFAULT_RESOURCE_ID, help="fetch-event resource_id to baseline"
    )


async def _baseline_job(ctx: JobContext) -> dict:
    """Harness handler; the session is the harness's owner-role one."""
    return await baseline_unbaselined(ctx.require_session(), resource_id=ctx.args.resource_id)


def main(argv: list[str] | None = None) -> int:
    """Baseline the pre-#54 fetch events under the **owner** role.

    The app role is REVOKEd UPDATE on the provenance ledger (#54), so this declares
    ``role="owner"`` and the harness resolves ``DATABASE_URL_OWNER``. Exit ``0`` clean ·
    ``1`` failed · ``2`` config. **Changed at #179b**: a missing ``DATABASE_URL_OWNER``
    used to escape as a bare ``RuntimeError`` traceback (exit 1) and is now the config
    exit ``2``, matching every other owner-role CLI.
    """
    return run_job(
        JOB_SLUG,
        _baseline_job,
        argv=argv,
        prog="python -m usa_wa_adapter_legislature.committees.migrate_fetch_baseline",
        description="Retroactively baseline pre-#54 committee fetch events (owner role).",
        extra_args=_add_args,
        role=DATABASE_ROLE_OWNER,
    )


if __name__ == "__main__":
    raise SystemExit(main())
