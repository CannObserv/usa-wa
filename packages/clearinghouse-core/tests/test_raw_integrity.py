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
    assert first["cursors"][""] is not None

    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--json"]) == 0
    second = json.loads(state_path.read_text())
    assert second["cursors"][""] != first["cursors"][""]

    # third pass reaches the tail and resets the cursor
    assert main(["--root", str(tmp_path), "--byte-budget", "1000000", "--json"]) == 0
    assert json.loads(state_path.read_text())["cursors"].get("") is None


def test_dry_run_does_not_persist_cursor(tmp_path, monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    _seed(tmp_path, [b"aaaa", b"bbbb"])
    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--dry-run", "--json"]) == 0
    assert not (tmp_path / ".raw_integrity_state.json").exists()


def test_missing_tail_past_cursor_is_reported(tmp_path, monkeypatch) -> None:
    """A cursor sitting just before a missing-only tail must not launder the
    corruption: the wrap pass merges the tail findings instead of replacing them."""
    patch_job_runtime(monkeypatch)
    store = _seed(tmp_path, [b"aaaa", b"bbbb"])
    objects = sorted(p for p in store.objects_dir.rglob("*") if p.is_file())
    head, tail = objects[0], objects[-1]
    tail.unlink()
    state_path = tmp_path / ".raw_integrity_state.json"
    state_path.write_text(json.dumps({"cursors": {"": ["src-a", head.name]}}) + "\n")
    # budget 4 = one object: the wrap pass exhausts on the head and never
    # re-reaches the tail, so only a merged result reports the corruption
    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--json"]) == 1


def test_legacy_cursor_shape_still_resumes(tmp_path, monkeypatch) -> None:
    """The pre-scoping state file ({"cursor": [...]}) reads as the unscoped cursor."""
    patch_job_runtime(monkeypatch)
    _seed(tmp_path, [b"aaaa", b"bbbb", b"cccc"])
    state_path = tmp_path / ".raw_integrity_state.json"
    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--json"]) == 0
    cursor = json.loads(state_path.read_text())["cursors"][""]
    state_path.write_text(json.dumps({"cursor": cursor}) + "\n")
    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--json"]) == 0
    assert json.loads(state_path.read_text())["cursors"][""] != cursor


def test_cursor_is_scoped_per_source(tmp_path, monkeypatch) -> None:
    """A --source B cursor must never skip --source A's keyspace (#302 CR)."""
    patch_job_runtime(monkeypatch)
    _seed(tmp_path, [b"aaaa", b"bbbb"])
    store_b = RawStore(tmp_path, "src-b")
    run = store_b.open_run()
    run.record("r0", b"zzzz", url="u")
    run.record("r1", b"yyyy", url="u")
    run.close()
    state_path = tmp_path / ".raw_integrity_state.json"

    # budget-limited scoped run on src-b persists a cursor for its own scope
    assert main(["--root", str(tmp_path), "--source", "src-b", "--byte-budget", "4", "--json"]) == 0
    state = json.loads(state_path.read_text())
    assert state["cursors"]["src-b"] is not None

    # corrupt src-a's first object; an unbudgeted scoped run on src-a must see it,
    # unaffected by src-b's persisted cursor
    store_a = RawStore(tmp_path, "src-a")
    [first, _second] = sorted(p for p in store_a.objects_dir.rglob("*") if p.is_file())
    first.write_bytes(b"tampered")
    argv = ["--root", str(tmp_path), "--source", "src-a", "--byte-budget", "1000000", "--json"]
    assert main(argv) == 1


def test_cursor_store_is_atomic_and_prunes_cleared_scopes(tmp_path, monkeypatch) -> None:
    """CR 43: the state write goes through tmp+replace (a crash cannot leave
    truncated JSON), and a scope whose cursor cleared is dropped, not kept as
    an accumulating null."""
    patch_job_runtime(monkeypatch)
    _seed(tmp_path, [b"aaaa", b"bbbb", b"cccc"])
    state_path = tmp_path / ".raw_integrity_state.json"
    assert main(["--root", str(tmp_path), "--byte-budget", "4", "--json"]) == 0
    assert json.loads(state_path.read_text())["cursors"][""] is not None
    # wrap: the cursor clears and the scope key is pruned
    assert main(["--root", str(tmp_path), "--byte-budget", "1000000", "--json"]) == 0
    state = json.loads(state_path.read_text())
    assert state["cursors"] == {}
    assert not list(tmp_path.glob(".raw_integrity_state.json.*.tmp"))
