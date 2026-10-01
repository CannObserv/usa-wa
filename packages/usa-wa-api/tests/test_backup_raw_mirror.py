"""The raw-store half of the #434 backup: mirror up, fetch back."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime

import pytest
from gcs_fakes import FakeBucket, FakeClient

from clearinghouse_core.rawstore import RawStore
from usa_wa_api.backup import raw_mirror
from usa_wa_api.backup.gcs import BackupError
from usa_wa_api.backup.raw_mirror import (
    fetch_mirror,
    local_inventory,
    mirror,
    unrecognized_files,
)

BUCKET = "a-backup-bucket"


def harvest(root, source: str, bodies: dict[str, bytes]) -> RawStore:
    """One closed run recording ``bodies`` under ``source``."""
    store = RawStore(root, source)
    run = store.open_run()
    for resource, body in bodies.items():
        run.record(
            resource, body, url=f"urn:{resource}", fetched_at=datetime(2026, 9, 1, tzinfo=UTC)
        )
    run.close()
    return store


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class TestLocalInventory:
    def test_objects_and_manifests_only(self, tmp_path) -> None:
        store = harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        (tmp_path / ".raw_integrity_state.json").write_text("{}")
        (store.objects_dir / sha(b"one")[:2] / f".{sha(b'one')}.abcd.tmp").write_bytes(b"half")
        inventory = local_inventory(tmp_path)
        run_id = store.manifest_paths()[0].name
        assert set(inventory) == {
            f"raw/usa_wa_sos/objects/{sha(b'one')[:2]}/{sha(b'one')}",
            f"raw/usa_wa_sos/runs/{run_id}",
        }

    def test_latest_json_and_locks_are_not_mirrored(self, tmp_path) -> None:
        """The index is mutable — a create-only object could never follow it — and a
        restore rebuilds it from the manifests."""
        harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        assert not any(
            key.endswith(("latest.json", ".latest.lock")) for key in local_inventory(tmp_path)
        )

    def test_expected_exclusions_are_not_unrecognized(self, tmp_path) -> None:
        store = harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        (tmp_path / ".raw_integrity_state.json").write_text("{}")
        (store.runs_dir / ".r.json.abcd.tmp").write_text("{}")
        assert unrecognized_files(tmp_path) == []

    def test_a_file_outside_the_layout_is_named(self, tmp_path) -> None:
        """A layout change the mirror does not know would otherwise stop being backed
        up without a word."""
        store = harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        (store.source_dir / "notes.txt").write_text("x")
        (store.objects_dir / "ab").mkdir(exist_ok=True)
        (store.objects_dir / "ab" / "not-a-sha").write_bytes(b"x")
        (tmp_path / "stray.json").write_text("{}")
        assert unrecognized_files(tmp_path) == [
            "stray.json",
            "usa_wa_sos/notes.txt",
            "usa_wa_sos/objects/ab/not-a-sha",
        ]

    def test_a_missing_root_is_an_empty_inventory(self, tmp_path) -> None:
        assert local_inventory(tmp_path / "absent") == {}


class TestScanOrder:
    def test_a_run_landing_mid_scan_never_ships_a_manifest_without_its_objects(
        self, tmp_path, monkeypatch
    ) -> None:
        """A run's manifest lands only after its objects. Listed manifests-first, any
        manifest the scan sees has its objects on disk by the time they are listed — so
        a harvest landing between the two passes costs a night's delay, never a manifest
        in the bucket whose objects are not."""
        harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        others = raw_mirror._others

        def a_run_lands_between_the_passes(root):
            harvest(root, "usa_wa_operator", {"m:departed:2020-01-01": b"late"})
            return others(root)

        monkeypatch.setattr(raw_mirror, "_others", a_run_lands_between_the_passes)
        inventory = local_inventory(tmp_path)

        objects = {key.rsplit("/", 1)[1] for key in inventory if "/objects/" in key}
        for key, path in inventory.items():
            if "/runs/" in key:
                shas = {e["sha256"] for e in json.loads(path.read_text())["entries"] if e["sha256"]}
                assert shas <= objects, f"{key} would ship without its objects"

    def test_one_scan_feeds_both_views(self, tmp_path) -> None:
        store = harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        (store.source_dir / "notes.txt").write_text("x")
        files, stray = raw_mirror.scan(tmp_path)
        assert files == local_inventory(tmp_path)
        assert stray == unrecognized_files(tmp_path) == ["usa_wa_sos/notes.txt"]


class TestMirror:
    def test_first_run_uploads_everything(self, tmp_path) -> None:
        harvest(tmp_path, "usa_wa_sos", {"r1": b"one", "r2": b"two"})
        bucket = FakeBucket()
        result = mirror(FakeClient(bucket), BUCKET, tmp_path, host="usa-wa")
        assert result.uploaded == 3
        assert result.mismatched == []
        key = f"raw/usa_wa_sos/objects/{sha(b'one')[:2]}/{sha(b'one')}"
        assert bucket.objects[key] == b"one"
        assert bucket.metadata[key] == {"source_host": "usa-wa", "sha256": sha(b"one")}
        assert bucket.preconditions == [0, 0, 0]

    def test_second_run_uploads_only_what_is_new(self, tmp_path) -> None:
        harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        bucket = FakeBucket()
        client = FakeClient(bucket)
        mirror(client, BUCKET, tmp_path, host="usa-wa")
        harvest(tmp_path, "usa_wa_sos", {"r1": b"one", "r2": b"two"})
        result = mirror(client, BUCKET, tmp_path, host="usa-wa")
        assert result.uploaded == 2  # the new object and the new manifest
        assert result.present == 2

    def test_a_corrupted_object_is_never_shipped(self, tmp_path) -> None:
        """The name is the integrity baseline: bytes that no longer hash to it are the
        integrity sweep's finding, and a backup that copied them would launder it."""
        store = harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        store.object_path(sha(b"one")).write_bytes(b"tampered")
        bucket = FakeBucket()
        result = mirror(FakeClient(bucket), BUCKET, tmp_path, host="usa-wa")
        assert result.mismatched == [f"raw/usa_wa_sos/objects/{sha(b'one')[:2]}/{sha(b'one')}"]
        assert not any("/objects/" in name for name in bucket.objects)

    def test_unrecognized_files_are_counted_and_never_uploaded(self, tmp_path) -> None:
        store = harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        (store.source_dir / "notes.txt").write_text("x")
        bucket = FakeBucket()
        result = mirror(FakeClient(bucket), BUCKET, tmp_path, host="usa-wa")
        assert result.unrecognized == ["usa_wa_sos/notes.txt"]
        assert result.uploaded == 2
        assert not any(name.endswith("notes.txt") for name in bucket.objects)

    def test_dry_run_plans_and_uploads_nothing(self, tmp_path) -> None:
        harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        bucket = FakeBucket()
        result = mirror(FakeClient(bucket), BUCKET, tmp_path, host="usa-wa", dry_run=True)
        assert result.planned == 2
        assert result.uploaded == 0
        assert bucket.objects == {}

    def test_objects_only_in_the_bucket_are_counted_not_touched(self, tmp_path) -> None:
        harvest(tmp_path, "usa_wa_sos", {"r1": b"one"})
        bucket = FakeBucket()
        bucket.put("raw/usa_wa_gone/runs/old.json", b"{}")
        result = mirror(FakeClient(bucket), BUCKET, tmp_path, host="usa-wa")
        assert result.remote_only == 1
        assert "raw/usa_wa_gone/runs/old.json" in bucket.objects


class TestFetchMirror:
    def test_round_trip_restores_the_store_and_its_index(self, tmp_path) -> None:
        source_root = tmp_path / "live"
        store = harvest(source_root, "usa_wa_sos", {"r1": b"one", "r2": b"two"})
        harvest(source_root, "usa_wa_operator", {"m:departed:2020-01-01": b"{}"})
        index = (store.source_dir / "latest.json").read_bytes()
        bucket = FakeBucket()
        mirror(FakeClient(bucket), BUCKET, source_root, host="usa-wa")

        dest = tmp_path / "restored"
        result = fetch_mirror(FakeClient(bucket), BUCKET, dest)

        assert result.fetched == 5
        assert result.latest_entries == {"usa_wa_operator": 1, "usa_wa_sos": 2}
        assert (dest / "usa_wa_sos" / "latest.json").read_bytes() == index
        assert RawStore(dest, "usa_wa_sos").object_path(sha(b"two")).read_bytes() == b"two"
        assert stat_mode(dest) == 0o700

    def test_an_object_that_does_not_hash_to_its_name_is_refused(self, tmp_path) -> None:
        bucket = FakeBucket()
        digest = sha(b"one")
        bucket.put(f"raw/usa_wa_sos/objects/{digest[:2]}/{digest}", b"tampered", {"sha256": digest})
        with pytest.raises(BackupError, match="does not hash to its name"):
            fetch_mirror(FakeClient(bucket), BUCKET, tmp_path / "restored")

    def test_a_manifest_that_does_not_match_its_recorded_digest_is_refused(self, tmp_path) -> None:
        bucket = FakeBucket()
        document = json.dumps({"run_id": "r", "entries": []}).encode()
        bucket.put("raw/usa_wa_sos/runs/r.json", document, {"sha256": sha(b"other")})
        with pytest.raises(BackupError, match="recorded"):
            fetch_mirror(FakeClient(bucket), BUCKET, tmp_path / "restored")

    def test_a_non_empty_destination_is_refused(self, tmp_path) -> None:
        dest = tmp_path / "restored"
        dest.mkdir(mode=0o700)
        (dest / "usa_wa_sos").mkdir()
        with pytest.raises(BackupError, match="not empty"):
            fetch_mirror(FakeClient(FakeBucket()), BUCKET, dest)

    def test_an_empty_mirror_is_a_failure_not_an_empty_restore(self, tmp_path) -> None:
        with pytest.raises(BackupError, match="no raw objects"):
            fetch_mirror(FakeClient(FakeBucket()), BUCKET, tmp_path / "restored")

    def test_a_name_outside_the_layout_is_refused(self, tmp_path) -> None:
        """Every name becomes a path under the destination; one that escapes it, or
        that the mirror never writes, is not followed."""
        bucket = FakeBucket()
        bucket.put("raw/usa_wa_sos/../../etc/passwd", b"x", {"sha256": sha(b"x")})
        with pytest.raises(BackupError, match="layout"):
            fetch_mirror(FakeClient(bucket), BUCKET, tmp_path / "restored")


def stat_mode(path) -> int:
    return os.stat(path).st_mode & 0o777
