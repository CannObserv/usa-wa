"""The bucket-facing helpers the #434 backup and restore share."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone

import pytest
from gcs_fakes import FakeBucket, FakeClient

from usa_wa_api.backup.gcs import (
    BackupError,
    create_object,
    iso,
    list_names,
    misplaced_key,
    preflight,
    sha256_file,
)


def test_iso_is_utc_second_precision_with_z() -> None:
    at = datetime(2026, 10, 1, 3, 4, 5, 678901, tzinfo=timezone(timedelta(hours=-7)))
    assert iso(at) == "2026-10-01T10:04:05Z"


def test_sha256_file(tmp_path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    assert sha256_file(path) == hashlib.sha256(b"abc").hexdigest()


class TestMisplacedKey:
    def test_outside_a_unit_there_is_nothing_to_compare(self) -> None:
        assert misplaced_key({"GOOGLE_APPLICATION_CREDENTIALS": "/etc/usa-wa/key.json"}) is None

    def test_the_credential_copy_is_accepted(self) -> None:
        environ = {
            "CREDENTIALS_DIRECTORY": "/run/credentials/usa-wa-backup.service",
            "GOOGLE_APPLICATION_CREDENTIALS": "/run/credentials/usa-wa-backup.service/gcs",
        }
        assert misplaced_key(environ) is None

    def test_an_env_file_aiming_at_the_root_only_original_is_named(self) -> None:
        """An EnvironmentFile= line beats the unit's Environment=, and the SDK then fails
        "Permission denied" on a 0400 root key — which reads as a reason to loosen it."""
        environ = {
            "CREDENTIALS_DIRECTORY": "/run/credentials/usa-wa-backup.service",
            "GOOGLE_APPLICATION_CREDENTIALS": "/etc/usa-wa/co-usa-wa-backup.json",
        }
        reason = misplaced_key(environ)
        assert reason is not None
        assert "/etc/usa-wa/backup.env" in reason

    def test_an_unset_key_under_a_unit_is_named(self) -> None:
        assert misplaced_key({"CREDENTIALS_DIRECTORY": "/run/credentials/x"}) is not None


class TestPreflight:
    def test_a_listable_bucket_passes(self) -> None:
        client = FakeClient()
        preflight(client, "a-backup-bucket")
        assert client.listings == [{"max_results": 1, "prefix": None}]

    def test_a_missing_bucket_fails_on_iteration_not_on_the_call(self) -> None:
        """The SDK's listing is lazy; a preflight that never advanced it would pass."""
        with pytest.raises(BackupError, match="not found"):
            preflight(FakeClient(missing=True), "a-backup-bucket")


class TestCreateObject:
    def test_creates_with_metadata_and_a_zero_generation_precondition(self, tmp_path) -> None:
        bucket = FakeBucket()
        path = tmp_path / "f"
        path.write_bytes(b"body")
        outcome = create_object(
            FakeClient(bucket),
            "a-backup-bucket",
            "k",
            path,
            sha256="digest",
            metadata={"x": "1"},
            content_type="application/json",
        )
        assert outcome == "uploaded"
        assert bucket.objects["k"] == b"body"
        assert bucket.metadata["k"] == {"x": "1", "sha256": "digest"}
        assert bucket.content_types["k"] == "application/json"
        assert bucket.preconditions == [0]

    def test_the_same_bytes_already_there_are_unchanged(self, tmp_path) -> None:
        bucket = FakeBucket()
        bucket.put("k", b"body", {"sha256": "digest"})
        path = tmp_path / "f"
        path.write_bytes(b"body")
        outcome = create_object(
            FakeClient(bucket), "a-backup-bucket", "k", path, sha256="digest", metadata={}
        )
        assert outcome == "unchanged"

    def test_different_bytes_under_the_name_are_a_collision(self, tmp_path) -> None:
        """Create-only means a name is never reused: anything but this very file is a
        failure, never an overwrite."""
        bucket = FakeBucket()
        bucket.put("k", b"other", {"sha256": "other-digest"})
        path = tmp_path / "f"
        path.write_bytes(b"body")
        with pytest.raises(BackupError, match="different contents"):
            create_object(
                FakeClient(bucket), "a-backup-bucket", "k", path, sha256="digest", metadata={}
            )
        assert bucket.objects["k"] == b"other"


def test_list_names_under_a_prefix() -> None:
    bucket = FakeBucket()
    for name in ("raw/a/objects/x", "raw/b/runs/y.json", "db/host/z.dump"):
        bucket.put(name, b"")
    assert list_names(FakeClient(bucket), "a-backup-bucket", "raw/") == {
        "raw/a/objects/x",
        "raw/b/runs/y.json",
    }


def test_the_fake_records_creation_time() -> None:
    bucket = FakeBucket()
    bucket.put("k", b"", created=datetime(2026, 1, 1, tzinfo=UTC))
    assert bucket.get_blob("k").time_created == datetime(2026, 1, 1, tzinfo=UTC)
