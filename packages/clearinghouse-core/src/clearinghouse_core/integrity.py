"""Provenance integrity sweep (#54) — at-rest tamper / corruption detection.

Re-hashes every stored :class:`~clearinghouse_core.provenance.RawPayload` body
against the :class:`~clearinghouse_core.provenance.FetchEvent.content_hash`
baseline written at fetch time (item 1). A mismatch means the archived bytes no
longer hash to what was recorded — corruption or tampering. NULL baselines are
the pre-#54 legacy tail: "unbaselined", counted apart and never treated as a
mismatch (a NULL is not a verified zero, and an all-zeros sentinel would be a
collision target — see the ``content_hash`` docstring).

``content_hash`` is defined over *exactly* ``RawPayload.body``, so the sweep
hashes the stored bytes directly — the same canonical form the writer used.

Run as a CLI (jurisdiction-agnostic; siblings reuse it)::

    python -m clearinghouse_core.integrity            # full sweep
    python -m clearinghouse_core.integrity --limit 500  # partial (surfaced as limited)

Exit codes: ``0`` clean (all baselined rows verified; unbaselined allowed);
``1`` at least one mismatch (the failure the #49 alert path surfaces). The
weekly oneshot's ``OnFailure=`` turns a non-zero exit into an operator email.
"""

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import FetchEvent, RawPayload

logger = get_logger(__name__)


@dataclass
class SweepReport:
    """Outcome of one integrity sweep."""

    scanned: int = 0
    verified: int = 0
    unbaselined: int = 0
    mismatched: int = 0
    limited: bool = False
    mismatches: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no body diverged from its baseline. Unbaselined rows are not
        failures — they had no baseline to diverge from."""
        return self.mismatched == 0


async def sweep_payloads(session: AsyncSession, *, limit: int | None = None) -> SweepReport:
    """Re-hash stored payload bodies against their content_hash baseline.

    Streams rows so a large archive doesn't load every body at once. ``limit``
    caps the scan for a quick partial check; the cap is surfaced on the report
    (``limited``) so a bounded run never reads as full coverage.
    """
    report = SweepReport()
    stmt = (
        select(
            RawPayload.body,
            FetchEvent.content_hash,
            FetchEvent.resource_id,
            FetchEvent.id,
        )
        .join(FetchEvent, RawPayload.fetch_event_id == FetchEvent.id)
        .order_by(RawPayload.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
        report.limited = True

    result = await session.stream(stmt)
    async for body, content_hash, resource_id, fetch_event_id in result:
        report.scanned += 1
        if content_hash is None:
            report.unbaselined += 1
            continue
        if hashlib.sha256(body).digest() == content_hash:
            report.verified += 1
            continue
        report.mismatched += 1
        report.mismatches.append(
            {"resource_id": resource_id, "fetch_event_id": str(fetch_event_id)}
        )
        logger.error(
            "integrity_sweep_mismatch",
            extra={"resource_id": resource_id, "fetch_event_id": str(fetch_event_id)},
        )
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m clearinghouse_core.integrity",
        description="Re-hash stored RawPayload bodies against their content_hash baseline (#54).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of payloads scanned (partial sweep; surfaced as limited).",
    )
    return parser


async def _run(args: argparse.Namespace) -> SweepReport:
    factory = get_session_factory()
    async with factory() as session:
        return await sweep_payloads(session, limit=args.limit)


def main(argv: list[str] | None = None) -> int:
    """Run the sweep, print the report as JSON, and exit non-zero on any mismatch.

    Exit codes: ``0`` clean; ``1`` at least one mismatch (corruption/tamper). The
    one-shot's ``OnFailure=`` (#49) emails the operator on the non-zero exit."""
    configure_logging()
    args = _build_parser().parse_args(argv)
    report = asyncio.run(_run(args))
    json.dump(
        {
            "scanned": report.scanned,
            "verified": report.verified,
            "unbaselined": report.unbaselined,
            "mismatched": report.mismatched,
            "limited": report.limited,
            "mismatches": report.mismatches,
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    if not report.ok:
        logger.error("integrity_sweep_failed", extra={"mismatched": report.mismatched})
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
