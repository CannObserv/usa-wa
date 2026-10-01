"""The #434 restore: find, fetch and prove, load, then check identity survived."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime

import pytest
from gcs_fakes import CREATED, FakeBucket, FakeClient
from test_backup_dump import FULL, FakeRunner, data_sql
from test_backup_raw_mirror import harvest
from ulid import ULID

from clearinghouse_core.job import EXIT_CONFIG, EXIT_FAILED, EXIT_OK
from usa_wa_api.backup import restore
from usa_wa_api.backup.gcs import BackupError
from usa_wa_api.backup.raw_mirror import mirror
from usa_wa_api.backup.restore import (
    Snapshot,
    as_user,
    check_crosswalk,
    check_restored,
    fetch_dump,
    latest_key,
    list_snapshots,
)

BUCKET = "a-backup-bucket"
ARCHIVE = b"PGDMP-archive-bytes"
REGISTRY_ROWS = {t: 3 for t in FULL if t.startswith("registry.")}


def ship(bucket: FakeBucket, name: str, body: bytes = ARCHIVE, **meta) -> None:
    """A dump as the backup would have shipped it."""
    metadata = {
        "sha256": hashlib.sha256(body).hexdigest(),
        "alembic_head": "abc123",
        "registry_rows": json.dumps(REGISTRY_ROWS),
        "dumped_at": "2026-10-01T10:17:03Z",
        "source_host": "usa-wa",
    } | meta
    bucket.put(name, body, metadata)


class RestoreRunner(FakeRunner):
    """The backup's runner, plus the restore's own psql queries and pg_restore."""

    def __init__(self, *, empty: bool = True, counts=None, head="abc123", keys=(), **kw) -> None:
        super().__init__(head=head, **kw)
        self.empty = empty
        self.counts = counts if counts is not None else REGISTRY_ROWS
        self.keys = keys
        self.stdin: list[bytes] = []

    def __call__(self, argv, **kwargs):
        command = argv[argv.index("--") + 1 :] if "--" in argv else argv
        sql = next(
            (a.removeprefix("--command=") for a in command if a.startswith("--command=")), ""
        )
        if command[0] == "pg_restore" and "--single-transaction" in command:
            self.calls.append(list(argv))
            self.stdin.append(kwargs["stdin"].read())
            if self.fail == "restore":
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="restore: boom\n")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if "pg_class" in sql:
            self.calls.append(list(argv))
            return subprocess.CompletedProcess(
                argv, 0, stdout="0\n" if self.empty else "52\n", stderr=""
            )
        if "UNION ALL" in sql:
            self.calls.append(list(argv))
            out = "".join(f"{t}|{n}\n" for t, n in self.counts.items())
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
        if "entity_keys" in sql:
            self.calls.append(list(argv))
            out = "".join(f"person,{k},{e},{m or ''}\n" for k, e, m in self.keys)
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
        return super().__call__(command, **kwargs)


def test_as_user_drops_privilege_without_a_session() -> None:
    assert as_user(["psql"], None) == ["psql"]
    assert as_user(["psql"], "postgres") == [
        "setpriv",
        "--reuid=postgres",
        "--regid=postgres",
        "--init-groups",
        "--reset-env",
        "--",
        "psql",
    ]


class TestSnapshots:
    def test_listing_is_name_ordered_and_carries_metadata(self) -> None:
        bucket = FakeBucket()
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        ship(bucket, "db/usa-wa/20260930T101500Z.dump")
        ship(bucket, "db/other/20261001T090000Z.dump")
        bucket.put("raw/usa_wa_sos/runs/r.json", b"{}")
        names = [s.name for s in list_snapshots(FakeClient(bucket), BUCKET, "usa-wa")]
        assert names == ["db/usa-wa/20260930T101500Z.dump", "db/usa-wa/20261001T101703Z.dump"]
        assert len(list_snapshots(FakeClient(bucket), BUCKET, None)) == 3

    def test_a_name_later_than_the_bucket_created_it_is_suspect(self) -> None:
        """A compromised writer planting ``2099…`` would otherwise own --latest."""
        honest = Snapshot("db/h/20261001T102000Z.dump", {}, CREATED)
        forged = Snapshot("db/h/20991231T000000Z.dump", {}, CREATED)
        skewed = Snapshot("db/h/20261001T103500Z.dump", {}, CREATED)
        assert not honest.suspect
        assert forged.suspect
        assert not skewed.suspect  # within the ten-minute clock-skew tolerance
        assert Snapshot("db/h/not-a-time.dump", {}, CREATED).suspect

    def test_latest_passes_over_suspect_names(self) -> None:
        bucket = FakeBucket()
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        ship(bucket, "db/usa-wa/20991231T000000Z.dump")
        assert latest_key(FakeClient(bucket), BUCKET, "usa-wa") == "db/usa-wa/20261001T101703Z.dump"

    def test_latest_of_nothing_is_an_error(self) -> None:
        with pytest.raises(BackupError, match="no dumps"):
            latest_key(FakeClient(), BUCKET, "usa-wa")


class TestFetchDump:
    def test_fetches_and_proves_the_object(self, tmp_path) -> None:
        bucket = FakeBucket()
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        path, meta = fetch_dump(
            FakeClient(bucket),
            BUCKET,
            "db/usa-wa/20261001T101703Z.dump",
            tmp_path / "d",
            runner=RestoreRunner(),
        )
        assert path.read_bytes() == ARCHIVE
        assert path.stat().st_mode & 0o777 == 0o600
        assert meta["alembic_head"] == "abc123"

    def test_a_digest_mismatch_is_refused_and_removed(self, tmp_path) -> None:
        bucket = FakeBucket()
        ship(bucket, "db/usa-wa/x.dump", sha256="0" * 64)
        with pytest.raises(BackupError, match="does not match"):
            fetch_dump(
                FakeClient(bucket),
                BUCKET,
                "db/usa-wa/x.dump",
                tmp_path / "d",
                runner=RestoreRunner(),
            )
        assert list((tmp_path / "d").iterdir()) == []

    def test_counts_that_disagree_with_the_recorded_ones_are_refused(self, tmp_path) -> None:
        """The archive's own COPY rows against what the backup recorded at dump time."""
        bucket = FakeBucket()
        ship(bucket, "db/usa-wa/x.dump")
        runner = RestoreRunner(data=data_sql(FULL | {"registry.entities": 2}))
        with pytest.raises(BackupError, match="registry.entities"):
            fetch_dump(
                FakeClient(bucket), BUCKET, "db/usa-wa/x.dump", tmp_path / "d", runner=runner
            )

    def test_an_object_without_a_recorded_digest_is_refused(self, tmp_path) -> None:
        bucket = FakeBucket()
        bucket.put("db/usa-wa/x.dump", ARCHIVE, {})
        with pytest.raises(BackupError, match="no recorded sha256"):
            fetch_dump(
                FakeClient(bucket),
                BUCKET,
                "db/usa-wa/x.dump",
                tmp_path / "d",
                runner=RestoreRunner(),
            )

    def test_an_absent_object(self, tmp_path) -> None:
        with pytest.raises(BackupError, match="not found"):
            fetch_dump(
                FakeClient(), BUCKET, "db/usa-wa/x.dump", tmp_path / "d", runner=RestoreRunner()
            )

    def test_a_shared_directory_is_refused(self, tmp_path) -> None:
        bucket = FakeBucket()
        ship(bucket, "db/usa-wa/x.dump")
        shared = tmp_path / "shared"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        with pytest.raises(BackupError, match="not private"):
            fetch_dump(
                FakeClient(bucket), BUCKET, "db/usa-wa/x.dump", shared, runner=RestoreRunner()
            )


class TestCheckRestored:
    META = {"alembic_head": "abc123", "registry_rows": json.dumps(REGISTRY_ROWS)}

    def test_a_faithful_restore_has_no_problems(self) -> None:
        assert check_restored("scratch", self.META, run_as=None, runner=RestoreRunner()) == []

    def test_a_count_off_by_one_is_named(self) -> None:
        runner = RestoreRunner(counts=REGISTRY_ROWS | {"registry.adjudications": 2})
        problems = check_restored("scratch", self.META, run_as=None, runner=runner)
        assert problems == ["registry.adjudications: 2 rows restored, 3 in the dump"]

    def test_a_schema_version_mismatch_is_named(self) -> None:
        problems = check_restored(
            "scratch", self.META, run_as=None, runner=RestoreRunner(head="zzz\n")
        )
        assert problems == ["alembic head zzz, dump recorded abc123"]


#: The dump the crosswalk tests restore, and publishes either side of it.
DUMPED_AT = datetime(2026, 10, 1, 10, 17, 3, tzinfo=UTC)
BEFORE = "v20260930T080505Z-a09d08"
AFTER = "v20261002T080505Z-b1c2d3"


def published(root, kind: str, rows, version: str = BEFORE, *, intact: bool = True) -> None:
    """One ``<kind>_crosswalk`` version holding ``rows``, as the publisher lays it out:
    ``data.csv`` beside a ``datapackage.json`` carrying its hash. ``intact=False`` is a
    publish that crashed before the data was whole."""
    target = root / f"{kind}_crosswalk" / version
    target.mkdir(parents=True)
    data = target / "data.csv"
    with data.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["entity_id", "natural_key", "registered_by", "merged_into"])
        for key, entity, merged in rows:
            writer.writerow([entity, key, "seed", merged or ""])
    digest = hashlib.sha256(data.read_bytes()).hexdigest()
    if not intact:
        data.write_text(data.read_text()[:-5])
    package = {"resources": [{"path": "data.csv", "hash": f"sha256:{digest}"}]}
    (target / "datapackage.json").write_text(json.dumps(package))


def crosswalk(root, keys, *, dumped_at=DUMPED_AT):
    return check_crosswalk(
        "scratch", root, dumped_at=dumped_at, run_as=None, runner=RestoreRunner(keys=keys)
    )


def ids(n: int) -> tuple[str, str]:
    """A ULID and the UUID text Postgres returns for it."""
    value = ULID.from_uuid(uuid.UUID(int=n))
    return str(value), str(value.to_uuid())


class TestCheckCrosswalk:
    def test_every_published_key_resolves_to_its_published_ulid(self, tmp_path) -> None:
        a, a_uuid = ids(1)
        b, b_uuid = ids(2)
        published(
            tmp_path, "person", [("usa_wa_legislature:1", a, None), ("usa_wa_legislature:2", b, a)]
        )
        published(tmp_path, "org", [])
        keys = [("usa_wa_legislature:1", a_uuid, None), ("usa_wa_legislature:2", b_uuid, a_uuid)]
        result = crosswalk(tmp_path, keys)
        assert result["problems"] == []
        assert result["person"] == {
            "version": BEFORE,
            "published": 2,
            "matched": 2,
            "missing": 0,
            "reassigned": 0,
            "merge_changed": 0,
            "newer": 0,
        }

    def test_a_reassigned_ulid_is_a_problem_a_later_merge_is_not(self, tmp_path) -> None:
        """ULIDs are sticky; a merge can land after the last publish."""
        a, a_uuid = ids(1)
        b, b_uuid = ids(2)
        c, c_uuid = ids(3)
        published(tmp_path, "person", [("k:1", a, None), ("k:2", b, None), ("k:3", c, None)])
        published(tmp_path, "org", [])
        keys = [("k:1", b_uuid, None), ("k:2", b_uuid, a_uuid), ("k:4", c_uuid, None)]
        result = crosswalk(tmp_path, keys)
        assert result["person"]["reassigned"] == 1
        assert result["person"]["merge_changed"] == 1
        assert result["person"]["missing"] == 1
        assert result["person"]["newer"] == 1
        assert result["problems"] == [
            "person: 1 published key(s) missing (k:3)",
            "person: 1 published key(s) on a different ULID (k:1)",
        ]

    def test_one_query_reads_both_crosswalks(self, tmp_path) -> None:
        """No per-kind query, so nothing is interpolated into the SQL."""
        a, a_uuid = ids(1)
        published(tmp_path, "person", [("k:1", a, None)])
        published(tmp_path, "org", [])
        runner = RestoreRunner(keys=[("k:1", a_uuid, None)])
        check_crosswalk("scratch", tmp_path, dumped_at=DUMPED_AT, run_as=None, runner=runner)
        queries = [c for c in runner.calls if any("entity_keys" in arg for arg in c)]
        assert len(queries) == 1
        assert not any("'person'" in arg or "'org'" in arg for arg in queries[0])

    def test_a_publish_after_the_dump_is_not_the_reference(self, tmp_path) -> None:
        """Restoring a dump older than the newest publish (a rollback, or a night the
        backup failed but the pipeline published) must not read every key registered
        since as missing."""
        a, a_uuid = ids(1)
        b, _ = ids(2)
        published(tmp_path, "person", [("k:1", a, None)], BEFORE)
        published(tmp_path, "person", [("k:1", a, None), ("k:2", b, None)], AFTER)
        published(tmp_path, "org", [])
        result = crosswalk(tmp_path, [("k:1", a_uuid, None)])
        assert result["problems"] == []
        assert result["person"]["version"] == BEFORE

    def test_a_partial_publish_is_passed_over(self, tmp_path) -> None:
        """A crash mid-publish leaves a version directory whose data is not whole."""
        a, a_uuid = ids(1)
        published(tmp_path, "person", [("k:1", a, None)], "v20260929T080505Z-000000")
        published(tmp_path, "person", [("k:1", a, None), ("k:9", a, None)], BEFORE, intact=False)
        published(tmp_path, "org", [])
        result = crosswalk(tmp_path, [("k:1", a_uuid, None)])
        assert result["problems"] == []
        assert result["person"]["version"] == "v20260929T080505Z-000000"

    def test_nothing_published_before_the_dump_is_a_skip_not_a_pass(self, tmp_path) -> None:
        a, _ = ids(1)
        published(tmp_path, "person", [("k:1", a, None)], AFTER)
        result = crosswalk(tmp_path, [])
        assert result["problems"] == []
        assert result["person"]["skipped"].startswith("no complete person_crosswalk")
        assert result["org"]["skipped"].startswith("no complete org_crosswalk")

    def test_no_datasets_at_all_is_a_skip(self, tmp_path) -> None:
        result = crosswalk(tmp_path / "none", [])
        assert result["problems"] == []
        assert "skipped" in result["person"]


class TestMain:
    @pytest.fixture
    def wired(self, monkeypatch):
        bucket = FakeBucket()
        runner = RestoreRunner()
        monkeypatch.setattr(restore, "make_client", lambda: FakeClient(bucket))
        monkeypatch.setattr(restore, "run_command", runner)
        monkeypatch.setenv("USA_WA_BACKUP_BUCKET", BUCKET)
        return bucket, runner

    def test_list(self, wired, capsys) -> None:
        bucket, _ = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        assert restore.main(["--list"]) == EXIT_OK
        assert "db/usa-wa/20261001T101703Z.dump" in capsys.readouterr().out

    def test_latest_needs_a_prefix(self, wired) -> None:
        """The restoring host is rarely the one that shipped: its own name is the one
        prefix never wanted."""
        assert restore.main(["--latest", "--into", "scratch"]) == EXIT_CONFIG

    def test_a_restore_needs_a_destination(self, wired) -> None:
        assert restore.main(["--latest", "--prefix", "usa-wa"]) == EXIT_CONFIG

    @pytest.mark.parametrize(
        "argv",
        [
            ["--list", "--into", "scratch"],
            ["--list", "--download-only", "/tmp/x"],
            ["--raw-into", "/tmp/r", "--into", "scratch"],
            ["--raw-into", "/tmp/r", "--download-only", "/tmp/x"],
        ],
    )
    def test_a_destination_the_mode_would_ignore_is_refused(self, wired, argv) -> None:
        """Mid-incident, an ignored --into reads as a load that happened."""
        bucket, runner = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        assert restore.main(argv) == EXIT_CONFIG
        assert runner.calls == []

    def test_into_as_root_needs_run_as(self, wired, monkeypatch, capsys) -> None:
        """psql as root fails 'role "root" does not exist' — name the flag instead."""
        bucket, runner = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        monkeypatch.setattr(restore.os, "geteuid", lambda: 0)
        assert restore.main(["--latest", "--prefix", "usa-wa", "--into", "scratch"]) == EXIT_CONFIG
        assert "--run-as postgres" in capsys.readouterr().err
        assert runner.calls == []

    def test_into_as_a_non_root_operator_may_omit_run_as(self, wired, monkeypatch, tmp_path):
        """An operator whose own role can load needs no privilege drop."""
        bucket, _ = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        monkeypatch.setattr(restore.os, "geteuid", lambda: 1000)
        argv = [
            "--latest",
            "--prefix",
            "usa-wa",
            "--into",
            "scratch",
            "--datasets-root",
            str(tmp_path),
        ]
        assert restore.main(argv) == EXIT_OK

    def test_no_bucket(self, wired, monkeypatch) -> None:
        monkeypatch.delenv("USA_WA_BACKUP_BUCKET")
        assert restore.main(["--list"]) == EXIT_CONFIG

    def test_download_only(self, wired, tmp_path) -> None:
        bucket, _ = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        assert (
            restore.main(["--latest", "--prefix", "usa-wa", "--download-only", str(tmp_path / "d")])
            == EXIT_OK
        )
        assert (tmp_path / "d" / "20261001T101703Z.dump").read_bytes() == ARCHIVE

    def test_into_an_empty_database_restores_and_checks(self, wired, tmp_path) -> None:
        bucket, runner = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        argv = ["--latest", "--prefix", "usa-wa", "--into", "scratch", "--run-as", "postgres"]
        assert restore.main([*argv, "--datasets-root", str(tmp_path / "none")]) == EXIT_OK
        assert runner.stdin == [ARCHIVE]
        loaded = next(c for c in runner.calls if "--single-transaction" in c)
        assert loaded[:2] == ["setpriv", "--reuid=postgres"]
        assert "--dbname=scratch" in loaded

    def test_never_into_a_database_that_holds_anything(self, wired, monkeypatch) -> None:
        """``--into usa_wa`` by mistake must refuse before it loads a byte."""
        bucket, _ = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        runner = RestoreRunner(empty=False)
        monkeypatch.setattr(restore, "run_command", runner)
        assert restore.main(["--latest", "--prefix", "usa-wa", "--into", "usa_wa"]) == EXIT_FAILED
        assert runner.stdin == []

    def test_a_failed_load_fails(self, wired, monkeypatch, tmp_path) -> None:
        bucket, _ = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        monkeypatch.setattr(restore, "run_command", RestoreRunner(fail="restore"))
        argv = [
            "--latest",
            "--prefix",
            "usa-wa",
            "--into",
            "scratch",
            "--datasets-root",
            str(tmp_path),
        ]
        assert restore.main(argv) == EXIT_FAILED

    def test_a_failed_check_fails_the_restore(self, wired, monkeypatch, tmp_path) -> None:
        bucket, _ = wired
        ship(bucket, "db/usa-wa/20261001T101703Z.dump")
        monkeypatch.setattr(restore, "run_command", RestoreRunner(head="zzz\n"))
        argv = [
            "--latest",
            "--prefix",
            "usa-wa",
            "--into",
            "scratch",
            "--datasets-root",
            str(tmp_path),
        ]
        assert restore.main(argv) == EXIT_FAILED

    def test_raw_into(self, wired, tmp_path) -> None:
        bucket, _ = wired
        harvest(tmp_path / "live", "usa_wa_sos", {"r": b"one"})
        mirror(FakeClient(bucket), BUCKET, tmp_path / "live", host="usa-wa")
        assert restore.main(["--raw-into", str(tmp_path / "restored")]) == EXIT_OK
        assert (tmp_path / "restored" / "usa_wa_sos" / "latest.json").exists()

    def test_a_failed_raw_fetch_fails(self, wired, tmp_path) -> None:
        assert restore.main(["--raw-into", str(tmp_path / "restored")]) == EXIT_FAILED
