"""Operator attestations into the #304 raw store (#412 PR A).

Every operator write — a succession event (#107), a committee-succession link (#124) —
lands its serialized body in Postgres provenance (``FetchEvent`` + ``RawPayload``) under
the ``usa_wa_operator`` source. #412 retires those tables, so each body now also lands
in the raw store, under the same ``resource_id``, ``url`` and content type the #305
export gave the pre-2026-09-03 corpus: the operator source reads as one ledger across
the cutover.

**Buffered, flushed after commit.** The stores write inside the caller's transaction,
and a rolled-back write (``--dry-run``, a validation failure) must leave nothing behind
— no manifest and no object. So a store only :meth:`~PendingAttestations.add`\\ s; the
entry point that owns the commit calls :meth:`~PendingAttestations.flush` after it.

**Deduplicated against the newest record**, the raw-side twin of the Postgres dedup: a
byte-identical re-ingest adds nothing. Not ``record_fetch``, which the plan named: that
is the harvest loop (TTL fresh-skip, a fetcher to call); an attestation has neither.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from clearinghouse_core.rawstore import RawStore, get_raw_root
from clearinghouse_domain_legislative.operator_events import OPERATOR_SOURCE_SLUG

#: The content type every attestation body is recorded under, in Postgres and raw alike.
ATTESTATION_CONTENT_TYPE = "application/json"


def attestation_url(resource_id: str) -> str:
    """The ``urn:`` an attestation is recorded under — it was never fetched from a URL."""
    return f"urn:usa-wa-operator:{resource_id}"


@dataclass
class PendingAttestations:
    """Attestation bodies waiting for their transaction to commit."""

    store: RawStore
    _pending: list[tuple[str, bytes, datetime]] = field(default_factory=list)

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
