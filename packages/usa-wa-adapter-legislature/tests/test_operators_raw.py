"""Operator attestations into the raw store (#412 PR A)."""

import hashlib
import json
import threading
from datetime import UTC, datetime

import pytest

from clearinghouse_core.rawstore import RAW_ROOT_ENV, RawStore
from clearinghouse_domain_legislative.operator_events import OPERATOR_SOURCE_SLUG
from usa_wa_adapter_legislature.operators.raw import (
    ATTESTATION_CONTENT_TYPE,
    AttestationArchiveError,
    PendingAttestations,
    attestation_url,
    flush_after_commit,
)

_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
_SID = "29091:departed:2025-04-19"


def _entries(root) -> list[dict]:
    store = RawStore(root, OPERATOR_SOURCE_SLUG)
    return [
        entry
        for path in store.manifest_paths()
        for entry in json.loads(path.read_text())["entries"]
    ]


def test_flush_records_the_export_shape(tmp_path):
    """Same resource_id, url and content type the #305 export gave the pre-09-03 corpus,
    so the operator source reads as one ledger across the cutover."""
    body = b'{"kind": "departed"}'
    pending = PendingAttestations.for_operator(tmp_path)
    pending.add(_SID, body, _AT)

    manifest = pending.flush()

    assert manifest is not None and manifest.is_file()
    [entry] = _entries(tmp_path)
    assert entry == {
        "resource_id": _SID,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "fetched_at": "2026-09-25T12:00:00.000000Z",
        "url": f"urn:usa-wa-operator:{_SID}",
        "status": "ok",
        "content_type": "application/json",
    }
    store = RawStore(tmp_path, OPERATOR_SOURCE_SLUG)
    assert store.object_path(entry["sha256"]).read_bytes() == body
    assert store.latest()[_SID]["sha256"] == entry["sha256"]


def test_attestation_url_and_content_type_are_the_postgres_ones():
    assert attestation_url(_SID) == f"urn:usa-wa-operator:{_SID}"
    assert ATTESTATION_CONTENT_TYPE == "application/json"


def test_nothing_pending_writes_nothing(tmp_path):
    assert PendingAttestations.for_operator(tmp_path).flush() is None
    assert not (tmp_path / OPERATOR_SOURCE_SLUG).exists()


def test_unflushed_attestations_leave_no_trace(tmp_path):
    """A rolled-back write never flushes: no manifest, and no stray object either."""
    PendingAttestations.for_operator(tmp_path).add(_SID, b"{}", _AT)
    assert not (tmp_path / OPERATOR_SOURCE_SLUG).exists()


def test_a_body_identical_to_the_newest_is_not_rerecorded(tmp_path):
    """The raw-side twin of the Postgres dedup: a byte-identical re-ingest adds nothing."""
    first = PendingAttestations.for_operator(tmp_path)
    first.add(_SID, b"{}", _AT)
    first.flush()

    again = PendingAttestations.for_operator(tmp_path)
    again.add(_SID, b"{}", _AT)

    assert again.flush() is None
    assert len(_entries(tmp_path)) == 1


def test_a_repeat_within_one_batch_is_recorded_once(tmp_path):
    pending = PendingAttestations.for_operator(tmp_path)
    pending.add(_SID, b"{}", _AT)
    pending.add(_SID, b"{}", _AT)
    pending.flush()
    assert len(_entries(tmp_path)) == 1


def test_a_changed_body_is_recorded(tmp_path):
    """A changed evidence_url is fresh provenance, as it is in Postgres."""
    first = PendingAttestations.for_operator(tmp_path)
    first.add(_SID, b'{"evidence_url": "a"}', _AT)
    first.flush()

    changed = PendingAttestations.for_operator(tmp_path)
    changed.add(_SID, b'{"evidence_url": "b"}', _AT)
    changed.flush()

    assert len(_entries(tmp_path)) == 2


def test_flush_drains_the_buffer(tmp_path):
    pending = PendingAttestations.for_operator(tmp_path)
    pending.add(_SID, b"{}", _AT)
    pending.flush()
    assert pending.flush() is None
    assert len(RawStore(tmp_path, OPERATOR_SOURCE_SLUG).manifest_paths()) == 1


def test_for_operator_defaults_to_the_configured_raw_root(tmp_path, monkeypatch):
    monkeypatch.setenv(RAW_ROOT_ENV, str(tmp_path))
    pending = PendingAttestations.for_operator()
    assert pending.store.source_dir == tmp_path / OPERATOR_SOURCE_SLUG


async def test_flush_after_commit_writes_off_the_event_loop(tmp_path, monkeypatch):
    """The flush is file I/O; the handlers that call it are async (the #196 rule the
    ``--file`` read already follows)."""
    flush_threads: list[int] = []
    real_flush = PendingAttestations.flush

    def _recording(self):
        flush_threads.append(threading.get_ident())
        return real_flush(self)

    monkeypatch.setattr(PendingAttestations, "flush", _recording)
    pending = PendingAttestations.for_operator(tmp_path)
    pending.add(_SID, b"{}", _AT)

    await flush_after_commit(pending)

    assert flush_threads and threading.get_ident() not in flush_threads
    assert len(_entries(tmp_path)) == 1


async def test_a_failed_flush_says_the_database_write_committed(tmp_path, monkeypatch):
    """A disk error after the commit must not read as a failed write: it names what did
    land and how to finish the rest."""

    def _broken(self):
        raise PermissionError("raw/ is read-only")

    monkeypatch.setattr(PendingAttestations, "flush", _broken)
    pending = PendingAttestations.for_operator(tmp_path)
    pending.add(_SID, b"{}", _AT)

    with pytest.raises(AttestationArchiveError, match="committed.*clearinghouse_core.raw_export"):
        await flush_after_commit(pending)


async def test_any_failure_after_the_commit_is_an_archive_error(tmp_path):
    """Not only disk errors: a corrupt ``latest.json`` fails the flush with a
    ``JSONDecodeError``, and the write has committed all the same."""
    source_dir = tmp_path / OPERATOR_SOURCE_SLUG
    source_dir.mkdir()
    (source_dir / "latest.json").write_text("{not json")
    pending = PendingAttestations.for_operator(tmp_path)
    pending.add(_SID, b"{}", _AT)

    with pytest.raises(AttestationArchiveError, match="committed"):
        await flush_after_commit(pending)
