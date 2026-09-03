"""The raw-store integrity sweep CLI (#304).

The file-store successor to ``clearinghouse_core.integrity``: re-hash
manifest-referenced objects against their names, rolling byte budget with a
cursor persisted beside the store, exit ``1`` on any mismatch/missing object
(corruption/tamper — ``failed``, never ``degraded``).
"""

import json

from clearinghouse_core.raw_integrity import JOB_SLUG, main
from clearinghouse_core.rawstore import RawStore
from clearinghouse_core.testing import patch_job_runtime


def _seed(tmp_path, bodies: list[bytes]) -> RawStore:
    store = RawStore(tmp_path, "src-a")
    run = store.open_run()
    for i, body in enumerate(bodies):
        run.record(f"r{i}", body, url="u")
    run.close()
    return store


def test_slug_is_stable() -> None:
    assert JOB_SLUG == "raw-integrity-sweep"


def test_clean_store_exits_zero(tmp_path, monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    _seed(tmp_path, [b"one", b"two"])
    assert main(["--root", str(tmp_path), "--json"]) == 0


def test_corruption_exits_one(tmp_path, monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    store = _seed(tmp_path, [b"one"])
    [obj] = [p for p in store.objects_dir.rglob("*") if p.is_file()]
    obj.write_bytes(b"tampered")
    assert main(["--root", str(tmp_path), "--json"]) == 1


def test_empty_store_is_clean(tmp_path, monkeypatch) -> None:
    """An empty root is a store nothing has harvested into yet, not a failure."""
    patch_job_runtime(monkeypatch)
    assert main(["--root", str(tmp_path), "--json"]) == 0


def test_budget_cursor_advances_and_wraps(tmp_path, monkeypatch) -> None:
    """Partial passes persist a cursor and cover the whole store across runs;
    reaching the tail resets it for a fresh coverage cycle."""
    patch_job_runtime(monkeypatch)
    _seed(tmp_path, [b"aaaa", b"bbbb", b"cccc"])
    state_path = tmp_path / ".raw_integrity_state.json"

    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--json"]) == 0
    first = json.loads(state_path.read_text())
    assert first["cursor"] is not None

    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--json"]) == 0
    second = json.loads(state_path.read_text())
    assert second["cursor"] != first["cursor"]

    # third pass reaches the tail and resets the cursor
    assert main(["--root", str(tmp_path), "--byte-budget", "1000000", "--json"]) == 0
    assert json.loads(state_path.read_text())["cursor"] is None


def test_dry_run_does_not_persist_cursor(tmp_path, monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    _seed(tmp_path, [b"aaaa", b"bbbb"])
    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--dry-run", "--json"]) == 0
    assert not (tmp_path / ".raw_integrity_state.json").exists()
