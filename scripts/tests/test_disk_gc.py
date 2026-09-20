"""Tests for scripts/disk-gc.sh — the host disk garbage collector + sensor (#394).

#394 was filed after `pytest` died with ENOSPC mid-session. Three properties
separate this from "a cleanup script someone runs when they remember":

* **It prunes only what is provably unreferenced.** Every candidate is checked
  against the live process table (`/proc/*/cmdline`) before removal, because the
  reclaimable copies and the in-use ones sit side by side in the same directory —
  five VS Code server builds, two of them serving live windows; five copies of
  SocratiCode's node tree, two of them running. A size-or-mtime heuristic would
  delete a running editor's server.
* **It never touches repo data.** Retention for the published-dataset, `raw/`,
  dbt and cassette tiers is a *contract* question descoped to its own issue; this
  script reports those sizes and removes nothing from them. A GC that quietly
  started pruning archival products would pre-empt that decision.
* **It runs when the repo is broken.** The unit is deliberately exempt from the
  #87 branch guard and the #279 venv guard (UNGUARDED_SERVICES in
  test_unit_ordering), and the script is plain bash touching no virtualenv —
  disk pressure is *more* likely while a worktree is checked out or a venv is
  half-synced, which is exactly when a guarded unit would refuse to start.

Pure filesystem + process-table exercise: every root is redirected at a tmp_path
and `docker` at a stub, so nothing here can reach the real host.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "disk-gc.sh"  # scripts/tests/ → scripts/

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/cmdline").exists(),
    reason="liveness detection reads /proc; Linux only",
)


# ── harness ───────────────────────────────────────────────────────────────────


@pytest.fixture
def live_procs():
    """Spawn processes whose *command line* contains a given path, and reap them.

    The production signal is "some running process names this path", which is how
    a live VS Code server build and a running `_npx` install are distinguishable
    from their idle siblings. `bash -c <body> <path>` puts the path in argv
    without the shell interpreting it, so the fixture exercises the real
    mechanism rather than a mock of it.
    """
    started: list[subprocess.Popen] = []

    def _spawn(path: Path | str) -> subprocess.Popen:
        proc = subprocess.Popen(
            ["bash", "-c", "while :; do sleep 0.2; done", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # The process table must show it before the script under test reads /proc.
        deadline = time.time() + 5
        target = str(path).encode()
        while time.time() < deadline:
            try:
                if target in Path(f"/proc/{proc.pid}/cmdline").read_bytes():
                    break
            except OSError:  # pragma: no cover - race with a not-yet-visible pid
                pass
            time.sleep(0.05)
        else:  # pragma: no cover - the spawn itself failed
            pytest.fail(f"spawned process never showed {path} in its cmdline")
        started.append(proc)
        return proc

    yield _spawn

    for proc in started:
        proc.send_signal(signal.SIGKILL)
        proc.wait()


@pytest.fixture
def live_cwd():
    """Spawn a process *running inside* a directory without naming it in argv.

    CR 2: the evidence a command line gives is not the only evidence. A process
    started by a relative path after a chdir runs out of a tree it never spells
    out, and a liveness check that reads argv alone would `rm -rf` it.
    """
    started: list[subprocess.Popen] = []

    def _spawn(path: Path) -> subprocess.Popen:
        path.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            ["bash", "-c", "while :; do sleep 0.2; done"],
            cwd=str(path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                if Path(f"/proc/{proc.pid}/cwd").resolve() == path.resolve():
                    break
            except OSError:  # pragma: no cover - race with a not-yet-visible pid
                pass
            time.sleep(0.05)
        else:  # pragma: no cover - the spawn itself failed
            pytest.fail(f"spawned process never showed {path} as its cwd")
        started.append(proc)
        return proc

    yield _spawn

    for proc in started:
        proc.send_signal(signal.SIGKILL)
        proc.wait()


@pytest.fixture
def host(tmp_path):
    """A fake host tree: VS Code server root, plugin cache, npx cache, repo."""
    vscode = tmp_path / "vscode-server"
    (vscode / "cli" / "servers").mkdir(parents=True)
    plugins = tmp_path / "plugins"
    (plugins / "cache" / "socraticode" / "socraticode").mkdir(parents=True)
    npx = tmp_path / "_npx"
    npx.mkdir()
    repo = tmp_path / "repo"
    (repo / "data" / "datasets").mkdir(parents=True)
    (repo / "raw").mkdir(parents=True)
    return {"root": tmp_path, "vscode": vscode, "plugins": plugins, "npx": npx, "repo": repo}


def _fill(path: Path, kib: int = 64) -> Path:
    """Create `path` as a directory holding a file of roughly `kib` KiB."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "payload.bin").write_bytes(b"\0" * (kib * 1024))
    return path


def _docker_stub(tmp_path: Path, slim_label: str | None) -> Path:
    """A `docker` stand-in reporting the ollama image's slim label (or absence)."""
    stub = tmp_path / "docker"
    label = slim_label if slim_label is not None else ""
    stub.write_text(f'#!/bin/sh\n[ "$1" = "image" ] || exit 1\nprintf \'%s\' "{label}"\n')
    stub.chmod(0o755)
    return stub


def run_gc(host, *args, docker: Path | str = "", **env_overrides) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DISK_GC_VSCODE_ROOT": str(host["vscode"]),
        "DISK_GC_PLUGIN_ROOT": str(host["plugins"]),
        "DISK_GC_NPX_ROOT": str(host["npx"]),
        "DISK_GC_REPO": str(host["repo"]),
        "DISK_GC_DOCKER": str(docker),
        # Default the thresholds low so a real host's free space never decides
        # a test's exit code; the threshold tests set them explicitly.
        "DISK_GC_WARN_BYTES": "0",
        "DISK_GC_FAIL_BYTES": "0",
        **{k: str(v) for k, v in env_overrides.items()},
    }
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=120
    )


def report(host, *args, **kwargs) -> dict:
    result = run_gc(host, "--json", *args, **kwargs)
    assert result.returncode in (0, 1), f"rc={result.returncode} stderr={result.stderr}"
    return json.loads(result.stdout)


# ── the sensor half ───────────────────────────────────────────────────────────


def test_reports_free_space_for_the_mount(host):
    data = report(host)
    assert data["free_bytes"] > 0
    assert data["total_bytes"] >= data["free_bytes"]
    assert 0 <= data["used_pct"] <= 100


def test_healthy_above_both_thresholds(host):
    data = report(host, DISK_GC_WARN_BYTES=1, DISK_GC_FAIL_BYTES=1)
    assert data["status"] == "ok"
    assert run_gc(host, DISK_GC_WARN_BYTES=1, DISK_GC_FAIL_BYTES=1).returncode == 0


def test_warns_below_the_warn_threshold_without_failing(host):
    """A warn is reported but must not exit non-zero — OnFailure= would email."""
    huge = 2**62
    data = report(host, DISK_GC_WARN_BYTES=huge, DISK_GC_FAIL_BYTES=1)
    assert data["status"] == "warn"
    assert run_gc(host, DISK_GC_WARN_BYTES=huge, DISK_GC_FAIL_BYTES=1).returncode == 0


def test_fails_below_the_fail_threshold(host):
    """Exit 1 is the whole point: it routes to usa-wa-notify-failure@ (#49)."""
    huge = 2**62
    data = report(host, DISK_GC_WARN_BYTES=huge, DISK_GC_FAIL_BYTES=huge)
    assert data["status"] == "fail"
    assert run_gc(host, DISK_GC_WARN_BYTES=huge, DISK_GC_FAIL_BYTES=huge).returncode == 1


def test_sizes_survive_a_directory_du_cannot_fully_read(host):
    """CR 1. `du -sb` prints a total AND exits non-zero when it could not
    descend everywhere, so a naive `du … || echo 0` emits two lines. That broke
    the byte arithmetic with a syntax error and put a raw newline inside the
    JSON — the machine-readable contract failing exactly when the filesystem is
    degraded, which is when the sensor matters most."""
    idle = _fill(host["vscode"] / "cli" / "servers" / "Stable-idle0000", kib=64)
    unreadable = idle / "unreadable"
    _fill(unreadable, kib=16)
    unreadable.chmod(0o000)
    try:
        data = report(host)  # parses as JSON, or this test fails
        assert isinstance(data["reclaimable_bytes"], int)
        assert all(isinstance(c["bytes"], int) for c in data["reclaimable"])
    finally:
        unreadable.chmod(0o755)


def test_reports_repo_tier_sizes(host):
    _fill(host["repo"] / "data" / "datasets" / "citations", kib=128)
    _fill(host["repo"] / "raw" / "usa_wa_legislature", kib=64)
    tiers = report(host)["tiers"]
    assert tiers["datasets"] >= 128 * 1024
    assert tiers["raw"] >= 64 * 1024


def test_never_prunes_repo_data(host):
    """Retention for the repo tiers is descoped (#396); this GC only measures them."""
    dataset = _fill(host["repo"] / "data" / "datasets" / "citations" / "v20260101T000000Z-aaaaaa")
    raw = _fill(host["repo"] / "raw" / "usa_wa_legislature")
    run_gc(host, "--prune")
    assert dataset.exists()
    assert raw.exists()


# ── VS Code server builds ─────────────────────────────────────────────────────


def test_prunes_idle_vscode_server_builds(host):
    idle = _fill(host["vscode"] / "cli" / "servers" / "Stable-idle0000")
    run_gc(host, "--prune")
    assert not idle.exists()


def test_keeps_a_live_vscode_server_build(host, live_procs):
    live = _fill(host["vscode"] / "cli" / "servers" / "Stable-live0000")
    live_procs(live / "server" / "bin" / "code-server")
    run_gc(host, "--prune")
    assert live.exists()


def test_prunes_idle_beside_live(host, live_procs):
    """The reclaimable and the in-use sit in one directory — the real shape."""
    live = _fill(host["vscode"] / "cli" / "servers" / "Stable-live0000")
    idle = _fill(host["vscode"] / "cli" / "servers" / "Stable-idle0000")
    live_procs(live / "server" / "bin" / "code-server")
    run_gc(host, "--prune")
    assert live.exists()
    assert not idle.exists()


def test_keeps_a_build_a_process_only_runs_inside(host, live_cwd):
    """CR 2. Nothing names this path in argv — the only evidence is the
    process's cwd. Reading command lines alone would delete a live server."""
    live = _fill(host["vscode"] / "cli" / "servers" / "Stable-cwd00000")
    live_cwd(live / "server")
    run_gc(host, "--prune")
    assert live.exists()


def test_keeps_lru_json_which_is_not_a_build(host):
    lru = host["vscode"] / "cli" / "servers" / "lru.json"
    lru.write_text("[]")
    run_gc(host, "--prune")
    assert lru.exists()


def test_prunes_idle_code_cli_binaries(host, live_procs):
    live = host["vscode"] / "code-live0000"
    live.write_bytes(b"\0" * 4096)
    idle = host["vscode"] / "code-idle0000"
    idle.write_bytes(b"\0" * 4096)
    live_procs(live)
    run_gc(host, "--prune")
    assert live.exists()
    assert not idle.exists()


# ── Claude plugin cache ───────────────────────────────────────────────────────


def _installed(host, version: str) -> None:
    path = host["plugins"] / "cache" / "socraticode" / "socraticode" / version
    (host["plugins"] / "installed_plugins.json").write_text(
        json.dumps(
            {"version": 2, "plugins": {"socraticode@socraticode": [{"installPath": str(path)}]}}
        )
    )


def test_prunes_plugin_cache_versions_that_are_not_installed(host):
    _installed(host, "1.14.0")
    stale = _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.12.0")
    current = _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.14.0")
    run_gc(host, "--prune")
    assert not stale.exists()
    assert current.exists()


def test_leaves_the_plugin_cache_alone_without_a_readable_manifest(host):
    """No manifest means no evidence of what is installed — so remove nothing."""
    version = _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.12.0")
    run_gc(host, "--prune")
    assert version.exists()

    (host["plugins"] / "installed_plugins.json").write_text("{not json")
    run_gc(host, "--prune")
    assert version.exists()


def test_an_empty_manifest_is_absence_of_evidence_not_permission(host):
    """CR 3. A manifest that parses but names nothing installed would have
    cleared every cached version — ~646 MB apiece. A partially written manifest
    explains that state at least as well as a real empty install does."""
    (host["plugins"] / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {}})
    )
    version = _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.14.0")
    run_gc(host, "--prune")
    assert version.exists()


def test_keeps_a_live_plugin_cache_version_even_if_uninstalled(host, live_procs):
    """A superseded version still serving a running session outranks the manifest."""
    _installed(host, "1.14.0")
    superseded = _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.12.0")
    _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.14.0")
    live_procs(superseded / "node_modules" / ".bin" / "socraticode")
    run_gc(host, "--prune")
    assert superseded.exists()


# ── npx caches ────────────────────────────────────────────────────────────────


def test_prunes_idle_npx_caches_and_keeps_live_ones(host, live_procs):
    """#389's known limitation: the plugin keeps launching @latest, so these
    accrue ~457 MB per release with nothing bounding them."""
    live = _fill(host["npx"] / "aaaaaaaaaaaaaaaa")
    idle = _fill(host["npx"] / "bbbbbbbbbbbbbbbb")
    live_procs(live / "node_modules" / ".bin" / "socraticode")
    run_gc(host, "--prune")
    assert live.exists()
    assert not idle.exists()


# ── report-only is the default ────────────────────────────────────────────────


def test_report_mode_deletes_nothing(host):
    idle_build = _fill(host["vscode"] / "cli" / "servers" / "Stable-idle0000")
    idle_npx = _fill(host["npx"] / "bbbbbbbbbbbbbbbb")
    data = report(host)  # no --prune
    assert idle_build.exists()
    assert idle_npx.exists()
    assert data["pruned_bytes"] == 0
    assert data["pruned"] == []


def test_report_mode_still_names_what_it_would_reclaim(host):
    """A sensor that sees reclaimable space must say so, or the operator has to
    run the pruning form to find out whether pruning would help."""
    _fill(host["vscode"] / "cli" / "servers" / "Stable-idle0000", kib=128)
    data = report(host)
    assert data["reclaimable_bytes"] >= 128 * 1024
    assert any(c["kind"] == "vscode-server" for c in data["reclaimable"])


def test_prune_accounts_for_what_it_removed(host):
    _fill(host["vscode"] / "cli" / "servers" / "Stable-idle0000", kib=128)
    _fill(host["npx"] / "bbbbbbbbbbbbbbbb", kib=64)
    data = report(host, "--prune")
    assert data["pruned_bytes"] >= 192 * 1024
    assert {c["kind"] for c in data["pruned"]} == {"vscode-server", "npx-cache"}


# ── the ollama slim-image regression ──────────────────────────────────────────


def test_warns_when_the_ollama_image_lost_its_slim_label(host, tmp_path):
    """`docker pull ollama/ollama:latest` silently restores ~7.4 GB of GPU
    libraries the host has no device for. Nothing else would report that."""
    data = report(host, docker=_docker_stub(tmp_path, None))
    assert any("ollama" in w for w in data["warnings"])


def test_quiet_when_the_ollama_image_is_slim(host, tmp_path):
    stub = _docker_stub(tmp_path, "cpu-only-gpu-libs-stripped")
    data = report(host, docker=stub)
    assert not any("ollama" in w for w in data["warnings"])


def test_no_docker_is_not_a_warning(host):
    """Off the VM there is no stack to check; absence must not read as damage."""
    data = report(host, docker="")
    assert not any("ollama" in w for w in data["warnings"])


# ── worktrees are reported, never removed ─────────────────────────────────────


def test_reports_stale_worktrees_without_removing_them(host):
    """`.skills/worktree_venv=none` makes each worktree cost its own venv (~225 MB),
    but destroying one is gated by the using-git-worktrees Iron Law — a merge
    check this script cannot make. So: measure, name, and leave it alone."""
    worktree = _fill(host["repo"] / ".worktrees" / "feat-something", kib=128)
    data = report(host, "--prune")
    assert worktree.exists()
    assert any("feat-something" in w for w in data["warnings"])
    assert data["tiers"]["worktrees"] >= 128 * 1024


# ── contract ──────────────────────────────────────────────────────────────────


def test_script_is_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_runs_without_a_virtualenv(host):
    """The unit is exempt from the #279 venv guard precisely because this path
    must work when the venv does not. Guard that it never grows a dependency on
    `uv` or the project environment."""
    assert "uv run" not in SCRIPT.read_text()
    env_free = shutil.which("bash")
    assert env_free, "bash is the only interpreter this script may need"
    result = run_gc(host, "--json", VIRTUAL_ENV="", PATH="/usr/local/bin:/usr/bin:/bin")
    assert result.returncode == 0, result.stderr
