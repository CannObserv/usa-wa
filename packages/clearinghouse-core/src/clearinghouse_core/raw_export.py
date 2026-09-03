"""One-shot RawPayload corpus export (#305): Postgres archive → raw-tier files.

    python -m clearinghouse_core.raw_export [--root PATH] [--limit N] [--reset-cursor]

Migrates the payload-bearing history out of the provenance tables into the
#304 file store, preserving hashes and fetch timestamps so the whole archive
stays replayable by the #302 pipeline before the tables retire (#314 — the
tables are NOT dropped here, and the export is additive/read-only on Postgres).

Contracts:

- Every body is re-hashed and compared to ``FetchEvent.content_hash`` **before**
  it lands; a mismatch raises :class:`ExportMismatch` and fails the run —
  corruption is a finding for the DB integrity sweep, never laundered into the
  new store. A NULL baseline (the pre-#54 legacy tail) is exported and the
  manifest entry marked ``unbaselined: true`` — from then on the object's own
  sha256 name is its baseline, as for every raw-store object.
- Payload-less FetchEvents are dedup shares (#59) — the bytes exist under the
  covering event's hash; they are skipped, not errors.
- Resumable: ordered by ``FetchEvent.id``; the CLI persists the last exported
  id at ``<root>/.rawpayload_export_state.json`` and continues past it, so an
  interrupted run re-invoked completes the remainder (objects dedup anyway —
  re-exporting is only wasted manifest rows, never wrong data).
- ``latest.json`` cannot regress: the store keeps the newest ``fetched_at`` per
  resource, so exporting history under a live harvest is safe in either order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.provenance import FetchEvent, RawPayload, Source
from clearinghouse_core.rawstore import RawRun, RawStore, get_raw_root

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "rawpayload-export"

STATE_FILENAME = ".rawpayload_export_state.json"

_BATCH = 200


class ExportMismatch(RuntimeError):
    """A payload's bytes do not hash to their FetchEvent baseline (#54 posture)."""


async def export_corpus(
    session: AsyncSession,
    root: Path | str,
    *,
    after_event_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Export payload-bearing FetchEvents ordered by id. Returns counters +
    ``last_event_id`` (the resume cursor; ``None`` when nothing was selected)."""
    counters: dict[str, Any] = {"exported": 0, "unchanged": 0, "unbaselined": 0}
    last_event_id: str | None = None
    runs: dict[str, RawRun] = {}
    stores: dict[str, RawStore] = {}

    remaining = limit
    cursor = after_event_id
    try:
        while remaining is None or remaining > 0:
            batch_size = _BATCH if remaining is None else min(_BATCH, remaining)
            stmt = (
                select(FetchEvent, RawPayload, Source.slug)
                .join(RawPayload, RawPayload.fetch_event_id == FetchEvent.id)
                .join(Source, Source.id == FetchEvent.source_id)
                .order_by(FetchEvent.id)
                .limit(batch_size)
            )
            if cursor is not None:
                stmt = stmt.where(FetchEvent.id > cursor)
            rows = (await session.execute(stmt)).all()
            if not rows:
                break
            for event, payload, slug in rows:
                digest = hashlib.sha256(payload.body).digest()
                if event.content_hash is not None and digest != event.content_hash:
                    raise ExportMismatch(
                        f"fetch_event {event.id} ({slug}:{event.resource_id}): body hashes to "
                        f"{digest.hex()}, baseline {event.content_hash.hex()}"
                    )
                if slug not in runs:
                    stores[slug] = RawStore(root, slug)
                    runs[slug] = stores[slug].open_run()
                unbaselined = event.content_hash is None
                fetch = runs[slug].record(
                    event.resource_id,
                    payload.body,
                    url=event.url,
                    status=str(event.status),
                    content_type=payload.content_type,
                    fetched_at=event.fetched_at,
                    extra={"unbaselined": True} if unbaselined else None,
                )
                if unbaselined:
                    counters["unbaselined"] += 1
                counters["exported"] += 1
                if not fetch.newly_stored:
                    counters["unchanged"] += 1
                cursor = str(event.id)
                last_event_id = cursor
            if remaining is not None:
                remaining -= len(rows)
    finally:
        for run in runs.values():
            run.close()

    counters["last_event_id"] = last_event_id
    logger.info("rawpayload_export_batch_complete", extra=dict(counters))
    return counters


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=None, help="Raw store root (default USA_WA_RAW_ROOT).")
    parser.add_argument(
        "--limit", type=int, default=None, help="Max payloads this run (default: all remaining)."
    )
    parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="Ignore the persisted resume cursor and start from the beginning.",
    )


async def _export_job(ctx: JobContext) -> JobResult:
    root = Path(ctx.args.root) if ctx.args.root else get_raw_root()
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / STATE_FILENAME
    after = None
    if not ctx.args.reset_cursor and state_path.is_file():
        after = json.loads(state_path.read_text()).get("last_event_id")
    counters = await export_corpus(
        ctx.require_session(), root, after_event_id=after, limit=ctx.args.limit
    )
    if counters["last_event_id"] is not None:
        state_path.write_text(json.dumps({"last_event_id": counters["last_event_id"]}) + "\n")
    return JobResult.ok(counters)


def main(argv: list[str] | None = None) -> int:
    """Export the RawPayload corpus into the raw file store. Exit ``1`` on a
    baseline mismatch (via the harness's failure path)."""
    return run_job(
        JOB_SLUG,
        _export_job,
        argv=argv,
        prog="python -m clearinghouse_core.raw_export",
        description="One-shot hash-preserving export of the RawPayload corpus to the raw store.",
        extra_args=_add_args,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
