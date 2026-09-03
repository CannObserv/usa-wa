"""The raw-tier file store (#304): content-addressed objects + run manifests.

The file analog of ``RawPayload`` (objects) and ``FetchEvent`` (manifest
entries): pristine bytes stored once under their sha256, every fetch recorded
per run, a ``latest.json`` index for freshness decisions, and re-verification
of bytes against their names for the integrity sweep.
"""

import fcntl
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from clearinghouse_core.rawstore import RawStore, record_fetch, verify_store

BODY = b"<wire>hello</wire>"
SHA = hashlib.sha256(BODY).hexdigest()


@pytest.fixture
def store(tmp_path) -> RawStore:
    return RawStore(tmp_path, "wsl-test")


def test_put_object_content_addressed_and_deduped(store: RawStore) -> None:
    sha, newly = store.put_object(BODY)
    assert sha == SHA
    assert newly is True
    path = store.object_path(sha)
    assert path.is_file()
    assert path.read_bytes() == BODY
    # sharded layout keeps directories bounded
    assert path.parent.name == sha[:2]

    sha2, newly2 = store.put_object(BODY)
    assert sha2 == sha
    assert newly2 is False


def test_run_manifest_written_atomically_on_close(store: RawStore) -> None:
    run = store.open_run()
    fetch = run.record("committees:2025-26", BODY, url="https://wsl/x")
    assert fetch.sha256 == SHA
    assert fetch.newly_stored is True
    # nothing listed until close — a crashed harvest leaves no partial manifest
    assert store.manifest_paths() == []
    manifest_path = run.close()
    assert store.manifest_paths() == [manifest_path]

    manifest = json.loads(manifest_path.read_text())
    assert manifest["source"] == "wsl-test"
    assert manifest["run_id"] == run.run_id
    [entry] = manifest["entries"]
    assert entry["resource_id"] == "committees:2025-26"
    assert entry["sha256"] == SHA
    assert entry["bytes"] == len(BODY)
    assert entry["status"] == "ok"
    assert entry["url"] == "https://wsl/x"
    assert entry["fetched_at"].endswith("Z")


def test_identical_refetch_recorded_but_not_restored(store: RawStore) -> None:
    """skip_unchanged parity: the fetch is on the ledger, the bytes stored once."""
    run1 = store.open_run()
    run1.record("r", BODY, url="u")
    run1.close()
    run2 = store.open_run()
    fetch = run2.record("r", BODY, url="u")
    run2.close()
    assert fetch.newly_stored is False
    assert len(store.manifest_paths()) == 2
    objects = list(store.objects_dir.rglob("*"))
    assert len([p for p in objects if p.is_file()]) == 1


def test_latest_index_tracks_ok_only(store: RawStore) -> None:
    run = store.open_run()
    run.record("good", BODY, url="u")
    run.record("bad", None, url="u", status="err")
    run.close()

    latest = store.latest()
    assert latest["good"]["sha256"] == SHA
    assert "bad" not in latest


def test_is_fresh_honors_ttl(store: RawStore) -> None:
    run = store.open_run()
    run.record("r", BODY, url="u")
    run.close()
    now = datetime.now(UTC)
    assert store.is_fresh("r", ttl_days=1, now=now) is True
    assert store.is_fresh("r", ttl_days=1, now=now + timedelta(days=2)) is False
    assert store.is_fresh("never-fetched", ttl_days=1, now=now) is False


def test_err_record_carries_no_object(store: RawStore) -> None:
    run = store.open_run()
    fetch = run.record("r", None, url="u", status="err")
    run.close()
    assert fetch.sha256 is None
    manifest = json.loads(run.manifest_path.read_text())
    assert manifest["entries"][0]["sha256"] is None


def test_verify_store_green(store: RawStore, tmp_path) -> None:
    run = store.open_run()
    run.record("r", BODY, url="u")
    run.close()
    result = verify_store(tmp_path)
    assert result.mismatched == []
    assert result.missing == []
    assert result.objects_verified == 1


def test_verify_store_detects_corruption_and_absence(store: RawStore, tmp_path) -> None:
    run = store.open_run()
    run.record("r", BODY, url="u")
    other = b"other-body"
    run.record("r2", other, url="u")
    run.close()

    store.object_path(SHA).write_bytes(b"tampered")
    store.object_path(hashlib.sha256(other).hexdigest()).unlink()

    result = verify_store(tmp_path)
    assert result.mismatched == [SHA]
    assert result.missing == [hashlib.sha256(other).hexdigest()]


def test_verify_store_byte_budget_bounds_work(store: RawStore, tmp_path) -> None:
    run = store.open_run()
    for i in range(5):
        run.record(f"r{i}", f"body-{i}".encode(), url="u")
    run.close()
    result = verify_store(tmp_path, byte_budget=1)
    assert result.objects_verified == 1
    assert result.exhausted_budget is True
    full = verify_store(tmp_path)
    assert full.objects_verified == 5
    assert full.exhausted_budget is False


def test_verify_store_resumes_after_cursor(store: RawStore, tmp_path) -> None:
    """`after` starts strictly past a (source, sha) key — the rolling-cursor seam."""
    run = store.open_run()
    for i in range(3):
        run.record(f"r{i}", f"body-{i}".encode(), url="u")
    run.close()

    first = verify_store(tmp_path, byte_budget=1)
    assert first.objects_verified == 1
    assert first.exhausted_budget is True
    assert first.last_key is not None

    rest = verify_store(tmp_path, after=first.last_key)
    assert rest.objects_verified == 2
    assert rest.exhausted_budget is False
    assert rest.last_key != first.last_key


def test_latest_index_keeps_newest_fetched_at(store: RawStore) -> None:
    """A later RUN recording an OLDER fetch (the #305 corpus export) must not
    regress latest.json past what the live harvest already recorded."""
    live = store.open_run()
    live.record("r", BODY, url="u", fetched_at=datetime(2026, 9, 1, tzinfo=UTC))
    live.close()
    export = store.open_run()
    export.record("r", b"historical", url="u", fetched_at=datetime(2020, 1, 1, tzinfo=UTC))
    export.close()
    assert store.latest()["r"]["sha256"] == SHA
    assert store.latest()["r"]["fetched_at"].startswith("2026-09-01")


def test_update_latest_serializes_on_source_lock(store: RawStore) -> None:
    """Concurrent closes on one source must not lose each other's entries: the
    read-modify-write of ``latest.json`` holds an exclusive per-source flock."""
    run = store.open_run()
    run.record("r1", BODY, url="u")
    store.source_dir.mkdir(parents=True, exist_ok=True)
    lock_path = store.source_dir / ".latest.lock"
    with open(lock_path, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        worker = threading.Thread(target=run.close)
        worker.start()
        worker.join(timeout=0.3)
        assert worker.is_alive(), "close() must block while another holder owns the lock"
        fcntl.flock(held, fcntl.LOCK_UN)
        worker.join(timeout=5)
        assert not worker.is_alive()
    assert store.latest()["r1"]["sha256"] == SHA


class _Payload:
    def __init__(self, wire: bytes, content_type: str | None = None) -> None:
        self.wire = wire
        self.content_type = content_type


def _fresh_counters() -> dict[str, int]:
    return {"fetched": 0, "unchanged": 0, "skipped_fresh": 0, "errors": 0}


async def test_record_fetch_stores_and_counts(store: RawStore) -> None:
    run = store.open_run()
    counters = _fresh_counters()

    async def fetcher() -> _Payload:
        return _Payload(BODY, "text/xml")

    outcome = await record_fetch(
        run, store, "r1", "u", fetcher, counters, 0.0, log_event="test_fetch_failed"
    )
    assert outcome.payload is not None and outcome.payload.wire == BODY
    assert not outcome.skipped_fresh and not outcome.error
    assert counters == {"fetched": 1, "unchanged": 0, "skipped_fresh": 0, "errors": 0}
    run.close()
    assert store.latest()["r1"]["sha256"] == SHA


async def test_record_fetch_contains_errors(store: RawStore) -> None:
    run = store.open_run()
    counters = _fresh_counters()

    async def fetcher() -> _Payload:
        raise RuntimeError("wire down")

    outcome = await record_fetch(
        run, store, "r1", "u", fetcher, counters, 0.0, log_event="test_fetch_failed"
    )
    assert outcome.error and outcome.payload is None
    assert counters["errors"] == 1
    run.close()
    assert store.latest() == {}  # an err entry never advances the index


async def test_record_fetch_skips_fresh(store: RawStore) -> None:
    run = store.open_run()
    run.record("r1", BODY, url="u")
    run.close()
    counters = _fresh_counters()

    async def fetcher() -> _Payload:  # pragma: no cover - must not be called
        raise AssertionError("fresh resource must not be fetched")

    run2 = store.open_run()
    outcome = await record_fetch(
        run2, store, "r1", "u", fetcher, counters, 7.0, log_event="test_fetch_failed"
    )
    assert outcome.skipped_fresh and outcome.payload is None and not outcome.error
    assert counters["skipped_fresh"] == 1


async def test_record_fetch_counts_unchanged_refetch(store: RawStore) -> None:
    run = store.open_run()
    run.record("r1", BODY, url="u")
    run.close()
    counters = _fresh_counters()

    async def fetcher() -> _Payload:
        return _Payload(BODY)

    outcome = await record_fetch(
        run, store, "r1", "u", fetcher, counters, 0.0, log_event="test_fetch_failed"
    )
    assert not outcome.error
    assert counters["fetched"] == 1 and counters["unchanged"] == 1


async def test_record_fetch_refuses_a_none_wire(store: RawStore) -> None:
    """CR 44: a payload with wire=None is a broken transport contract — recording
    it "ok" would poison latest.json into TTL-freshening a resource with no
    stored bytes. Raise, never contain."""
    run = store.open_run()
    counters = _fresh_counters()

    async def fetcher() -> _Payload:
        return _Payload(None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="wire"):
        await record_fetch(
            run, store, "r1", "u", fetcher, counters, 0.0, log_event="test_fetch_failed"
        )
