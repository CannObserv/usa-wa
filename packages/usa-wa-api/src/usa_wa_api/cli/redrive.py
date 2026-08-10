"""CLI re-drive surface for dead-lettered (UNAVAILABLE) outbox entries.

A thin ``python -m usa_wa_api.cli.redrive`` wrapper over
:func:`usa_wa_api.api.redrive.perform_redrive`, for on-box operator use when the
HTTP route is inconvenient (e.g. during a maintenance window). Shares the exact
scoping / dry-run semantics of the endpoint; commits the transaction itself
(there is no request lifecycle to do it). No operator token is required — shell
access to the box is already the trust boundary.

Examples::

    python -m usa_wa_api.cli.redrive --dry-run
    python -m usa_wa_api.cli.redrive --entity-type person
    python -m usa_wa_api.cli.redrive --older-than-seconds 3600
    python -m usa_wa_api.cli.redrive --limit 50
"""

import argparse

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, run_job
from usa_wa_api.api.redrive import perform_redrive

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "outbox-redrive"


def _non_negative_int(value: str) -> int:
    """argparse type: a ``>= 0`` int, mirroring the HTTP route's ``Query(ge=0)``.

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
