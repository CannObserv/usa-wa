"""The raw-tier file store (#304): content-addressed objects + run manifests.

The file analog of ``RawPayload`` (objects) and ``FetchEvent`` (manifest
entries): pristine bytes stored once under their sha256, every fetch recorded
per run, a ``latest.json`` index for freshness decisions, and re-verification
of bytes against their names for the integrity sweep.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from clearinghouse_core.rawstore import RawStore, verify_store

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
