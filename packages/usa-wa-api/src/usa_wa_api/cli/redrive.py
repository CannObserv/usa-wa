"""CLI re-drive surface for dead-lettered (UNAVAILABLE) outbox entries.

``python -m usa_wa_api.cli.redrive`` — the ONLY re-drive surface since #313.
``POST /sync/redrive`` retired with the rest of the mutating API, so the
deployment is purely read-only and Power Map can revoke its write scopes against
an API that provably cannot write. Nothing was lost: this path has the same
scoping and dry-run semantics, and shell access to the box was always a stronger
trust boundary than the single shared ``X-Operator-Token`` it replaces.

It commits the transaction itself (there is no request lifecycle to do it).

``--dry-run`` is also the operator's **backlog count** now that ``/health/sync``
is gone: it reports ``matched``, the dead-lettered pile, without touching a row.
The wider picture — overdue PENDING work and the oldest entry's age — is logged
by the sidecar itself on every duty cycle, which is where it was always most
visible.

Examples::

    python -m usa_wa_api.cli.redrive --dry-run
    python -m usa_wa_api.cli.redrive --entity-type person
    python -m usa_wa_api.cli.redrive --older-than-seconds 3600
    python -m usa_wa_api.cli.redrive --limit 50
"""

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, run_job
from clearinghouse_sync_powermap.engine import SyncEngine

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "outbox-redrive"


async def perform_redrive(
    session: AsyncSession,
    *,
    entity_type: str | None = None,
    older_than_seconds: int | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict:
    """Re-drive scope-matched UNAVAILABLE outbox entries back to PENDING.

    The re-drive itself, since #313 reachable only from this CLI: the
    ``POST /sync/redrive`` route retired with the rest of the mutating surface,
    and shell access to the box is already the trust boundary that route's
    ``X-Operator-Token`` was approximating. ``dry_run`` returns the counts
    without mutating. ``limit`` caps the flip (oldest-first) while
    ``matched`` reports the full in-scope pile; ``would_redrive`` is the count a
    real call with these exact params would flip (``min(matched, limit)``), so a
    dry run previews the capped effect rather than the whole pile. Both the count
    and the flip defer to the engine (:meth:`SyncEngine.count_unavailable` /
    :meth:`redrive_unavailable`), so scope and reset semantics are never
    duplicated here. The clientless, registry-less engine is a safe, intentional
    shim — these two methods only touch ``session`` and never exercise any
    read/write path. Does not commit — the caller owns the transaction. Returns
    ``matched`` / ``would_redrive`` / ``redriven`` counts, the echoed filters, and
    the ``dry_run`` flag.
    """
    now = now or datetime.now(UTC)
    older_than = timedelta(seconds=older_than_seconds) if older_than_seconds is not None else None
    engine = SyncEngine(descriptors=(), client=None)

    matched = await engine.count_unavailable(
        session, now=now, entity_type=entity_type, older_than=older_than
    )
    would_redrive = min(matched, limit) if limit is not None else matched

    redriven = 0
    if not dry_run and matched:
        redriven = await engine.redrive_unavailable(
            session, now=now, entity_type=entity_type, older_than=older_than, limit=limit
        )

    return {
        "matched": matched,
        "would_redrive": would_redrive,
        "redriven": redriven,
        "dry_run": dry_run,
        "entity_type": entity_type,
        "older_than_seconds": older_than_seconds,
        "limit": limit,
    }


def _non_negative_int(value: str) -> int:
    """argparse type: a ``>= 0`` int, mirroring the retired route's ``Query(ge=0)``.

    A negative age would invert the filter (``created_at <= now + |X|``) and match
    every row — silently turning a scoped re-drive into an unscoped one.
    """
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _positive_int(value: str) -> int:
    """argparse type: a ``>= 1`` int, mirroring the HTTP route's ``Query(ge=1)``.

    A limit of 0 (or below) would flip nothing — reject it rather than silently
    no-op a re-drive the operator asked for.
    """
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute the re-drive's own flags to the harness's shared parser."""
    parser.add_argument(
        "--entity-type",
        default=None,
        help="Only re-drive entries of this entity type.",
    )
    parser.add_argument(
        "--older-than-seconds",
        type=_non_negative_int,
        default=None,
        help="Only re-drive entries created at least this many seconds ago.",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="Cap the number of entries re-driven (oldest first).",
    )


async def _run(
    session: AsyncSession,
    *,
    entity_type: str | None,
    older_than_seconds: int | None,
    limit: int | None,
    dry_run: bool,
) -> dict:
    """Perform the (scoped) re-drive in ``session``.

    Since #179b the session is the harness's, and so is the commit — this used to open
    its own and commit it.
    """
    return await perform_redrive(
        session,
        entity_type=entity_type,
        older_than_seconds=older_than_seconds,
        limit=limit,
        dry_run=dry_run,
    )


async def _redrive_job(ctx: JobContext) -> dict:
    """Harness handler; the harness owns the commit and the ``--dry-run`` rollback."""
    args = ctx.args
    return await _run(
        ctx.require_session(),
        entity_type=args.entity_type,
        older_than_seconds=args.older_than_seconds,
        limit=args.limit,
        dry_run=ctx.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    """Re-drive the dead-lettered outbox entries. Exit ``0`` clean · ``1`` failed · ``2`` config."""
    return run_job(
        JOB_SLUG,
        _redrive_job,
        argv=argv,
        prog="python -m usa_wa_api.cli.redrive",
        description="Re-drive dead-lettered (UNAVAILABLE) PM-sync outbox entries to PENDING.",
        extra_args=_add_args,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
