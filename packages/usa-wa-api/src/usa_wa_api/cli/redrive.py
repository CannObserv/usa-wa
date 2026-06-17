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
import asyncio
import json
import sys

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging
from usa_wa_api.api.redrive import perform_redrive


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_api.cli.redrive",
        description="Re-drive dead-lettered (UNAVAILABLE) PM-sync outbox entries to PENDING.",
    )
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the matched count without mutating any rows.",
    )
    return parser


async def _run(
    entity_type: str | None,
    older_than_seconds: int | None,
    limit: int | None,
    dry_run: bool,
) -> dict:
    """Open a session, perform the (scoped) re-drive, and commit."""
    factory = get_session_factory()
    async with factory() as session:
        result = await perform_redrive(
            session,
            entity_type=entity_type,
            older_than_seconds=older_than_seconds,
            limit=limit,
            dry_run=dry_run,
        )
        if not dry_run:
            await session.commit()
        return result


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the re-drive, and print the result as JSON. Returns exit code."""
    configure_logging()
    args = _build_parser().parse_args(argv)
    result = asyncio.run(_run(args.entity_type, args.older_than_seconds, args.limit, args.dry_run))
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
