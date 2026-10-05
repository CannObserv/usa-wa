"""Operator attestations into the #304 raw store (#412).

Every operator write — a succession event (#107), a committee-succession link (#124) —
lands its serialized body in the raw store under the ``usa_wa_operator`` source, with the
same ``resource_id``, ``url`` and content type the #305 export gave the Postgres-era
corpus: the operator source reads as one ledger across the cutover. PR A added this
store beside Postgres provenance (``FetchEvent`` + ``RawPayload``); PR F dropped those
tables, so the raw store is now the attestation's only provenance.

**Buffered, flushed after commit.** The stores write inside the caller's transaction,
and a rolled-back write (``--dry-run``, a validation failure) must leave nothing behind
— no manifest and no object. So a store only :meth:`~PendingAttestations.add`\\ s; the
entry point that owns the commit awaits :func:`flush_after_commit` after it — off the
event loop, since the flush is file I/O and those entry points are async handlers.

**Deduplicated against the newest record**: a byte-identical re-ingest adds nothing.
Deliberately *newest* rather than *any earlier* record: an attestation restated as X, then
Y, then X again gets a third entry, because the projection row carries X again and
``latest.json`` should name the bytes it holds.

Not ``record_fetch``, which the plan named: that is the harvest loop (TTL fresh-skip, a
fetcher to call); an attestation has neither.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from clearinghouse_core.logging import get_logger
from clearinghouse_core.rawstore import RawStore, get_raw_root
from clearinghouse_domain_legislative.operator_events import OPERATOR_SOURCE_SLUG

logger = get_logger(__name__)

#: The content type every attestation body is recorded under.
ATTESTATION_CONTENT_TYPE = "application/json"


class AttestationArchiveError(RuntimeError):
    """The database write committed, but its raw-store copy did not land."""


def attestation_url(resource_id: str) -> str:
    """The ``urn:`` an attestation is recorded under — it was never fetched from a URL."""
    return f"urn:usa-wa-operator:{resource_id}"


@dataclass
class PendingAttestations:
    """Attestation bodies waiting for their transaction to commit."""

    store: RawStore
    _pending: list[tuple[str, bytes, datetime]] = field(
        default_factory=list, init=False, repr=False
    )

    @classmethod
    def for_operator(cls, root: Path | str | None = None) -> PendingAttestations:
        """Buffer for the ``usa_wa_operator`` source under ``root`` (default: the
        configured raw root, ``USA_WA_RAW_ROOT``)."""
        return cls(RawStore(root if root is not None else get_raw_root(), OPERATOR_SOURCE_SLUG))

    def add(self, resource_id: str, body: bytes, fetched_at: datetime) -> None:
        """Buffer one attestation. Nothing touches the disk until :meth:`flush`."""
        self._pending.append((resource_id, body, fetched_at))

    def flush(self) -> Path | None:
        """Record every buffered body that is not already its resource's newest, as one
        run. Returns the manifest path, or ``None`` when there was nothing new."""
        newest = {rid: entry["sha256"] for rid, entry in self.store.latest().items()}
        run = None
        for resource_id, body, fetched_at in self._pending:
            sha = hashlib.sha256(body).hexdigest()
            if newest.get(resource_id) == sha:
                continue
            run = run or self.store.open_run()
            run.record(
                resource_id,
                body,
                url=attestation_url(resource_id),
                content_type=ATTESTATION_CONTENT_TYPE,
                fetched_at=fetched_at,
            )
            newest[resource_id] = sha
        self._pending.clear()
        return run.close() if run is not None else None


async def flush_after_commit(raw: PendingAttestations) -> Path | None:
    """Flush ``raw`` in a worker thread. Call it only once the transaction has committed.

    Any failure here comes **after** the commit — a disk error, a corrupt ``latest.json`` —
    so every one is re-raised as :class:`AttestationArchiveError`, whose message says the
    write landed: the caller reports the run degraded, not failed.

    The recovery is recording the event again as it now stands. A write is idempotent on
    its natural key and always buffers its body, and the flush deduplicates only against
    the resource's newest record, so the missing bytes land. Not the original command
    when it was a ``--supersede``: that refuses a prior it has already superseded, so the
    corrected event is recorded plainly instead. (Until #412 PR F the recovery was
    ``raw_export`` from the Postgres copy, which no longer exists.)
    """
    try:
        return await asyncio.to_thread(raw.flush)
    except Exception as exc:  # post-commit by construction, so every failure; logged below
        logger.exception("operator_raw_flush_failed", extra={"raw_root": str(raw.store.root)})
        raise AttestationArchiveError(
            f"the database write committed, but archiving it to {raw.store.source_dir} "
            f"failed ({exc}); record the event again as it now stands, without --supersede: "
            "the write is idempotent and archives its bytes"
        ) from exc


async def archive_after_commit(raw: PendingAttestations) -> bool:
    """The entry points' post-commit step: flush, report, and say whether it landed.

    Prints the manifest path when a run was written, or a ``warning:`` line with the
    recovery when it failed. ``False`` means the caller's run is degraded: its database
    write committed and only the raw copy is missing.
    """
    try:
        manifest = await flush_after_commit(raw)
    except AttestationArchiveError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        return False
    if manifest is not None:
        print(f"archived to {manifest}")
    return True
