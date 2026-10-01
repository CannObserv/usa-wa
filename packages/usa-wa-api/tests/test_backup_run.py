"""The #434 nightly job: preflight, dump + ship, mirror the raw store."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
from backup_fakes import FakeRunner, harvest
from gcs_fakes import FakeBucket, FakeClient

from clearinghouse_core.job import EXIT_CONFIG, EXIT_FAILED, EXIT_OK
from usa_wa_api.backup import run as backup_run
from usa_wa_api.backup.gcs import BackupError
from usa_wa_api.backup.run import run_backup

BUCKET = "a-backup-bucket"
NOW = datetime(2026, 10, 1, 10, 17, 3, tzinfo=UTC)


def backup(tmp_path, bucket: FakeBucket | None = None, **overrides):
    raw_root = tmp_path / "raw"
    if not raw_root.exists():
        harvest(raw_root, "usa_wa_operator", {"m:departed:2020-01-01": b"{}"})
    kwargs = {
        "client": FakeClient(bucket if bucket is not None else FakeBucket()),
        "bucket": BUCKET,
        "prefix": "usa-wa",
        "database": "usa_wa",
        "raw_root": raw_root,
        "workdir": tmp_path / "work",
        "runner": FakeRunner(),
        "host": "usa-wa",
        "now": lambda: NOW,
    } | overrides
    return run_backup(**kwargs)


class TestRunBackup:
    def test_ships_the_dump_and_the_raw_store(self, tmp_path) -> None:
        bucket = FakeBucket()
        counters = backup(tmp_path, bucket)
        key = "db/usa-wa/20261001T101703Z.dump"
        assert counters["db"]["object"] == f"gs://{BUCKET}/{key}"
        assert counters["db"]["outcome"] == "uploaded"
        assert counters["raw"]["uploaded"] == 2
        assert counters["failures"] == []
        assert bucket.metadata[key]["alembic_head"] == "abc123"
        assert json.loads(bucket.metadata[key]["registry_rows"])["registry.entities"] == 3

    def test_a_missing_bucket_fails_before_the_dump(self, tmp_path) -> None:
        runner = FakeRunner()
        with pytest.raises(BackupError, match="not found"):
            backup(tmp_path, client=FakeClient(missing=True), runner=runner)
        assert runner.calls == []

    def test_a_failed_dump_still_mirrors_the_raw_store(self, tmp_path) -> None:
        """Two independent halves: one failing must not cost the night the other."""
        bucket = FakeBucket()
        counters = backup(tmp_path, bucket, runner=FakeRunner(fail="pg_dump"))
        assert counters["failures"] == ["db: pg_dump exited 1: pg_dump: boom"]
        assert counters["raw"]["uploaded"] == 2
        assert not any(name.startswith("db/") for name in bucket.objects)

    def test_an_unexpected_error_keeps_its_traceback(self, tmp_path, caplog) -> None:
        """A programming error in one half is a one-line failure in the email — and a
        traceback in the journal, or nobody can debug it."""

        def broken(argv, **kwargs):
            raise TypeError("unexpected keyword")

        with caplog.at_level("ERROR", logger="usa_wa_api.backup.run"):
            counters = backup(tmp_path, runner=broken)
        assert counters["failures"] == ["db: TypeError: unexpected keyword"]
        [record] = [r for r in caplog.records if r.exc_info]
        assert record.exc_info[0] is TypeError

    def test_an_expected_failure_logs_no_traceback(self, tmp_path, caplog) -> None:
        with caplog.at_level("ERROR", logger="usa_wa_api.backup.run"):
            backup(tmp_path, runner=FakeRunner(fail="pg_dump"))
        assert not [r for r in caplog.records if r.exc_info]

    def test_a_corrupted_raw_object_is_a_failure(self, tmp_path) -> None:
        store = harvest(tmp_path / "raw", "usa_wa_sos", {"r": b"one"})
        store.object_path(hashlib.sha256(b"one").hexdigest()).write_bytes(b"tampered")
        counters = backup(tmp_path)
        assert counters["db"]["outcome"] == "uploaded"
        assert len(counters["failures"]) == 1
        assert counters["failures"][0].startswith("raw: 1 object(s) no longer hash")

    def test_a_file_outside_the_layout_fails_the_run(self, tmp_path) -> None:
        store = harvest(tmp_path / "raw", "usa_wa_sos", {"r": b"one"})
        (store.source_dir / "notes.txt").write_text("x")
        counters = backup(tmp_path)
        assert counters["raw"]["unrecognized"] == 1
        assert counters["failures"] == [
            "raw: 1 file(s) outside the mirrored layout, not shipped: usa_wa_sos/notes.txt "
            "— teach usa_wa_api.backup.raw_mirror the layout, or move them out of the store"
        ]

    def test_an_absent_raw_store_is_a_failure_not_an_empty_success(self, tmp_path) -> None:
        """A moved store or a renamed USA_WA_RAW_ROOT reads as zero files; the
        integrity sweep exits 4 on the same state for the same reason."""
        counters = backup(tmp_path, raw_root=tmp_path / "nowhere")
        assert counters["failures"] == [f"raw: no raw store at {tmp_path / 'nowhere'}"]

    def test_an_empty_raw_store_is_a_failure(self, tmp_path) -> None:
        (tmp_path / "empty").mkdir()
        counters = backup(tmp_path, raw_root=tmp_path / "empty")
        assert counters["failures"] == [f"raw: the store at {tmp_path / 'empty'} holds nothing"]

    def test_dry_run_dumps_and_verifies_but_ships_nothing(self, tmp_path) -> None:
        bucket = FakeBucket()
        counters = backup(tmp_path, bucket, dry_run=True)
        assert bucket.objects == {}
        assert counters["db"]["outcome"] == "dry-run"
        assert counters["db"]["registry_rows"]["registry.entities"] == 3
        assert counters["raw"]["planned"] == 2


class TestMain:
    @pytest.fixture
    def wired(self, tmp_path, monkeypatch):
        """``main`` with the SDK client and subprocess replaced, the raw root and the
        bucket configured — the unit's environment, minus the unit."""
        bucket = FakeBucket()
        harvest(tmp_path / "raw", "usa_wa_operator", {"m:departed:2020-01-01": b"{}"})
        monkeypatch.setattr(backup_run, "make_client", lambda: FakeClient(bucket))
        monkeypatch.setattr(backup_run, "run_command", FakeRunner())
        monkeypatch.setenv("USA_WA_RAW_ROOT", str(tmp_path / "raw"))
        monkeypatch.setenv("USA_WA_BACKUP_BUCKET", BUCKET)
        monkeypatch.setenv("USA_WA_BACKUP_PREFIX", "usa-wa")
        monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        return bucket

    def test_a_clean_run_exits_zero(self, wired) -> None:
        assert backup_run.main([]) == EXIT_OK
        assert any(name.startswith("db/usa-wa/") for name in wired.objects)

    def test_any_failure_exits_one(self, wired, monkeypatch) -> None:
        monkeypatch.setattr(backup_run, "run_command", FakeRunner(fail="pg_dump"))
        assert backup_run.main([]) == EXIT_FAILED

    def test_no_bucket_is_a_config_error_not_a_guess(self, wired, monkeypatch) -> None:
        monkeypatch.delenv("USA_WA_BACKUP_BUCKET")
        assert backup_run.main([]) == EXIT_CONFIG
        assert wired.objects == {}

    def test_a_misplaced_key_is_a_config_error(self, wired, monkeypatch) -> None:
        monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/run/credentials/usa-wa-backup.service")
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/etc/usa-wa/co-usa-wa-backup.json")
        assert backup_run.main([]) == EXIT_CONFIG

    def test_a_client_that_cannot_be_built_is_a_failure(self, wired, monkeypatch) -> None:
        def broken():
            raise OSError("key unreadable")

        monkeypatch.setattr(backup_run, "make_client", broken)
        assert backup_run.main([]) == EXIT_FAILED

    def test_dry_run_ships_nothing(self, wired) -> None:
        assert backup_run.main(["--dry-run"]) == EXIT_OK
        assert wired.objects == {}
