"""Tests for scripts/disk-gc.sh — the host disk garbage collector + sensor (#394).

#394 was filed after `pytest` died with ENOSPC mid-session. Three properties
separate this from "a cleanup script someone runs when they remember":

* **It prunes only what is provably unreferenced.** Every candidate is checked
  against the live process table before removal — `cmdline`, `cwd`, `exe` AND
  `maps`, because a process started by a relative path names its tree in none
  of its argv (CR 2), and one holding a native addon loaded names it in none of
  the other three (#399 CR 1) — since the reclaimable copies and the in-use
  ones sit side by side in the same directory: five VS Code server builds, two
  of them serving live windows; five copies of SocratiCode's node tree, two of
  them running; five Claude extension versions (#399), one live and a different
  one active. A size-or-mtime heuristic would delete a running editor's server.
  A *grace window* covers the case liveness cannot (CR 12): a tree still being
  installed is named by no process yet.
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
def live_exe():
    """Spawn a process *executing a binary* inside a tree, naming it nowhere else.

    #399: this is how the live Claude extension version is actually in use — the
    agent `exe`s its bundled native binary, and neither argv (argv[0] is a bare
    name here) nor cwd spells out the extension directory.
    """
    started: list[subprocess.Popen] = []

    def _spawn(binary: Path) -> subprocess.Popen:
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shutil.which("sleep"), binary)
        proc = subprocess.Popen(
            ["sleep", "600"],
            executable=str(binary),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                if Path(f"/proc/{proc.pid}/exe").resolve() == binary.resolve():
                    break
            except OSError:  # pragma: no cover - race with a not-yet-visible pid
                pass
            time.sleep(0.05)
        else:  # pragma: no cover - the spawn itself failed
            pytest.fail(f"spawned process never showed {binary} as its exe")
        started.append(proc)
        return proc

    yield _spawn

    for proc in started:
        proc.send_signal(signal.SIGKILL)
        proc.wait()


@pytest.fixture
def live_mmap():
    """Spawn a process that holds a file from a tree *mapped*, and nothing else.

    #399 CR 1: an un-reloaded VS Code window's extension host still has an old
    Claude extension version loaded, and names that directory in none of
    cmdline, cwd or exe — its one trace is the native addon in its memory maps.
    The path reaches the child through the environment, which the liveness
    snapshot does not read, and the descriptor is closed once mapped.
    """
    started: list[subprocess.Popen] = []
    body = (
        "import mmap, os, time\n"
        "with open(os.environ['MAPPED'], 'rb') as fh:\n"
        "    held = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)\n"
        "print('mapped', flush=True)\n"
        "time.sleep(600)\n"
    )

    def _spawn(path: Path) -> subprocess.Popen:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\0" * 4096)
        proc = subprocess.Popen(
            ["python3", "-c", body],
            env={**os.environ, "MAPPED": str(path)},
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if proc.stdout.readline().strip() != "mapped":  # pragma: no cover
            pytest.fail(f"spawned process never mapped {path}")
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
        # Fixtures create their trees milliseconds before the run, so the
        # production grace window would (correctly) protect every one of them.
        # Disabled by default; the grace-window test sets it explicitly.
        "DISK_GC_GRACE_MINUTES": "0",
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


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through chmod 000, exercising nothing")
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


def test_keeps_a_tree_that_is_still_being_written(host):
    """CR 12. Liveness cannot answer this: a tree being installed right now is
    named by no running process, because the process that will run out of it
    does not exist yet. #389 measured a ~457 MB `_npx` install at session start,
    and the timer fires daily — so a session starting a minute earlier would
    have its half-written install deleted under it."""
    fresh = _fill(host["npx"] / "aaaaaaaaaaaaaaaa")
    data = report(host, "--prune", DISK_GC_GRACE_MINUTES=60)
    assert fresh.exists()
    assert data["pruned"] == []


@pytest.mark.parametrize(
    "setting", ["DISK_GC_GRACE_MINUTES", "DISK_GC_WARN_BYTES", "DISK_GC_FAIL_BYTES"]
)
def test_bad_configuration_refuses_to_run(host, setting):
    """CR 19. These all used to fail OPEN: a non-numeric value made bash print
    `integer expression expected` and then SKIP the check, so a typo in the
    unit's Environment= silently disarmed the guard while the run continued and
    exited 0. A misconfigured GC must refuse, not run with its safeties off."""
    result = run_gc(host, "--json", **{setting: "abc"})
    assert result.returncode == 2
    assert setting in result.stderr
    assert result.stdout == ""


def test_a_bad_grace_value_cannot_prune(host):
    """The consequence the refusal exists to prevent."""
    tree = _fill(host["npx"] / "aaaaaaaaaaaaaaaa")
    run_gc(host, "--prune", DISK_GC_GRACE_MINUTES="")
    assert tree.exists()


def test_withheld_candidates_are_reported_not_silently_skipped(host):
    """CR 21. `reclaimed: 0B in 0 item(s)` was byte-identical whether the window
    had withheld half a gigabyte or there was nothing to do — true either way,
    and misleading in the one tool someone opens when the disk is filling."""
    _fill(host["npx"] / "aaaaaaaaaaaaaaaa", kib=128)
    data = report(host, "--prune", DISK_GC_GRACE_MINUTES=60)
    assert data["pruned"] == []
    assert [c["kind"] for c in data["withheld"]] == ["npx-cache"]
    assert data["grace_minutes"] == 60

    human = run_gc(host, "--prune", DISK_GC_GRACE_MINUTES=60)
    assert "withheld: 1 item(s)" in human.stdout


def test_nothing_withheld_on_an_empty_host(host):
    data = report(host, "--prune", DISK_GC_GRACE_MINUTES=60)
    assert data["withheld"] == []


def test_prunes_a_tree_older_than_the_grace_window(host):
    """The other half of CR 12 — the window must expire, or nothing is ever
    reclaimed and the GC quietly becomes a no-op."""
    old = _fill(host["npx"] / "bbbbbbbbbbbbbbbb")
    stale = time.time() - 7200
    for path in [old, *old.rglob("*")]:
        os.utime(path, (stale, stale))
    run_gc(host, "--prune", DISK_GC_GRACE_MINUTES=60)
    assert not old.exists()


def test_a_fresh_file_deep_inside_protects_the_tree(host):
    """An installer writing files inside leaves the top level's own mtime
    untouched, so the check has to look at the whole tree."""
    tree = _fill(host["npx"] / "cccccccccccccccc")
    stale = time.time() - 7200
    os.utime(tree, (stale, stale))
    (tree / "node_modules").mkdir()
    (tree / "node_modules" / "just-written").write_bytes(b"x")
    run_gc(host, "--prune", DISK_GC_GRACE_MINUTES=60)
    assert tree.exists()


def test_keeps_a_build_a_process_only_runs_inside(host, live_cwd):
    """CR 2. Nothing names this path in argv — the only evidence is the
    process's cwd. Reading command lines alone would delete a live server."""
    live = _fill(host["vscode"] / "cli" / "servers" / "Stable-cwd00000")
    live_cwd(live / "server")
    run_gc(host, "--prune")
    assert live.exists()


def test_a_full_disk_cannot_blind_the_liveness_check(host, live_procs):
    """CR 5. The snapshot used to be a temp file on the very disk this GC runs to
    rescue: at ENOSPC the write truncated silently, every process it lost read
    as idle, and --prune ran `rm -rf` on trees in use — in exactly the state the
    unit exists for. `ulimit -f 1` makes any file write past 1 KiB fail, which
    is the same truncation; the live build must survive it."""
    live = _fill(host["vscode"] / "cli" / "servers" / "Stable-live0000")
    live_procs(live / "server" / "bin" / "code-server")
    env = {
        **os.environ,
        "DISK_GC_VSCODE_ROOT": str(host["vscode"]),
        "DISK_GC_PLUGIN_ROOT": str(host["plugins"]),
        "DISK_GC_NPX_ROOT": str(host["npx"]),
        "DISK_GC_REPO": str(host["repo"]),
        "DISK_GC_DOCKER": "",
        "DISK_GC_WARN_BYTES": "0",
        "DISK_GC_FAIL_BYTES": "0",
        "DISK_GC_GRACE_MINUTES": "0",
    }
    subprocess.run(
        ["bash", "-c", 'ulimit -f 1 && exec "$0" --prune', str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
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


def _plugin_warnings(data: dict) -> list[str]:
    return [w for w in data["warnings"] if "installed_plugins.json" in w]


def test_prunes_plugin_cache_versions_that_are_not_installed(host):
    _installed(host, "1.14.0")
    stale = _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.12.0")
    current = _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.14.0")
    data = report(host, "--prune")
    assert not stale.exists()
    assert current.exists()
    assert _plugin_warnings(data) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root removes through a read-only parent")
def test_a_failed_removal_stays_in_the_reclaimable_accounting(host):
    """CR 7. Counting a candidate as neither pruned nor reclaimable made a run
    that failed to free 2 GB report `pruned_bytes: 0, reclaimable_bytes: 0` —
    read as "nothing to reclaim", the opposite of what happened."""
    servers = host["vscode"] / "cli" / "servers"
    _fill(servers / "Stable-idle0000", kib=128)
    servers.chmod(0o555)  # removal denied, listing still allowed
    try:
        data = report(host, "--prune")
        assert data["pruned_bytes"] == 0
        assert data["reclaimable_bytes"] >= 128 * 1024
        assert any(c["kind"] == "vscode-server" for c in data["reclaimable"])
        assert any("could not remove" in w for w in data["warnings"])
    finally:
        servers.chmod(0o755)


def _write_plugin_manifest(host, case: str) -> None:
    manifest = host["plugins"] / "installed_plugins.json"
    # Every shape case names the version that IS on disk, so it can only pass
    # by the shape being refused — not by naming nothing.
    on_disk = {
        "installPath": str(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.14.0")
    }
    if case == "absent":
        return
    if case == "unparseable":
        manifest.write_text('{"version": 2, "plugins": {"socraticode@socraticode": [{"inst')
    elif case == "names-nothing":
        manifest.write_text(json.dumps({"version": 2, "plugins": {}}))
    elif case == "names-a-version-not-on-disk":
        _installed(host, "1.15.0")
    elif case == "not-an-object":
        manifest.write_text(json.dumps([on_disk]))
    elif case == "wrong-shape-after-a-valid-entry":
        # The parser prints the valid path, then crashes: what it printed
        # before the crash is a partial list, not evidence.
        plugins = {"socraticode@socraticode": [on_disk], "other@market": "not-a-list"}
        manifest.write_text(json.dumps({"version": 2, "plugins": plugins}))


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "unparseable",
        "names-nothing",
        "names-a-version-not-on-disk",
        "not-an-object",
        "wrong-shape-after-a-valid-entry",
    ],
)
def test_no_usable_manifest_removes_no_plugin_version(host, case):
    """The manifest is the only evidence of which version is installed, so
    every way of lacking it means remove *nothing* (CR 3): a partially written
    manifest explains an empty one at least as well as a real empty install
    does, and each tree is ~646 MB. One naming a version that is not on disk —
    a half-finished install, a manifest written ahead of extraction — is no
    better (#400): pruning the rest would leave the plugin nothing to run. And
    the refusal is reported, or the sensor prints `0B` over all of it."""
    cache = host["plugins"] / "cache" / "socraticode" / "socraticode"
    versions = [_fill(cache / "1.12.0"), _fill(cache / "1.14.0")]
    _write_plugin_manifest(host, case)
    data = report(host, "--prune")
    assert all(v.exists() for v in versions)
    assert data["pruned"] == []
    assert len(_plugin_warnings(data)) == 1


def test_no_plugin_cache_is_not_a_warning(host):
    """A host with no cached plugin version has nothing to evaluate; absence of
    the tier must not read as a broken manifest."""
    assert _plugin_warnings(report(host)) == []


def test_keeps_a_live_plugin_cache_version_even_if_uninstalled(host, live_procs):
    """A superseded version still serving a running session outranks the manifest."""
    _installed(host, "1.14.0")
    superseded = _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.12.0")
    _fill(host["plugins"] / "cache" / "socraticode" / "socraticode" / "1.14.0")
    live_procs(superseded / "node_modules" / ".bin" / "socraticode")
    run_gc(host, "--prune")
    assert superseded.exists()


# ── VS Code extensions (#399) ─────────────────────────────────────────────────

CLAUDE_EXT = "anthropic.claude-code"


def _extension(host, version: str, kib: int = 64) -> Path:
    """One Claude extension version, laid out as VS Code installs it."""
    return _fill(host["vscode"] / "extensions" / f"{CLAUDE_EXT}-{version}-linux-x64", kib=kib)


def _manifest_entry(root: Path, ext_id: str, version: str) -> dict:
    """One `extensions.json` entry, in the shape VS Code writes it."""
    rel = f"{ext_id}-{version}-linux-x64"
    return {
        "identifier": {"id": ext_id},
        "version": version,
        "location": {
            "$mid": 1,
            "fsPath": str(root / rel),
            "path": str(root / rel),
            "scheme": "file",
        },
        "relativeLocation": rel,
        "metadata": {"source": "gallery", "targetPlatform": "linux-x64"},
    }


def _active_extension(host, version: str, root: Path | None = None) -> None:
    """Write `extensions.json` naming `version` as the active Claude extension."""
    root = root or host["vscode"] / "extensions"
    root.mkdir(parents=True, exist_ok=True)
    (root / "extensions.json").write_text(json.dumps([_manifest_entry(root, CLAUDE_EXT, version)]))


def _extension_warnings(data: dict) -> list[str]:
    return [w for w in data["warnings"] if "extensions.json" in w]


def test_extension_versions_are_a_gc_root(host):
    """The defect #399 names: 649 MB of superseded extension binaries sat in a
    tier the sensor did not look at, so it reported `reclaimable: 0B` — and #394
    was filed after ENOSPC at 565 MB free."""
    _active_extension(host, "2.1.278")
    _extension(host, "2.1.278")
    _extension(host, "2.1.259", kib=128)
    data = report(host)
    reclaimable = [c for c in data["reclaimable"] if c["kind"] == "vscode-extension"]
    assert [Path(c["path"]).name for c in reclaimable] == [f"{CLAUDE_EXT}-2.1.259-linux-x64"]
    assert reclaimable[0]["bytes"] >= 128 * 1024


def test_prunes_extension_versions_neither_active_nor_live(host, live_exe):
    """The host as #399 measured it: idle versions beside one a running agent
    executes out of, beside the one the manifest names. Live and active are
    different states and a version can be either without the other."""
    _active_extension(host, "2.1.278")
    idle = _extension(host, "2.1.259")
    live = _extension(host, "2.1.273")
    active = _extension(host, "2.1.278")
    live_exe(live / "resources" / "native-binary" / "claude")
    run_gc(host, "--prune")
    assert not idle.exists()
    assert live.exists()
    assert active.exists()


def test_keeps_a_version_an_unreloaded_window_still_has_loaded(host, live_mmap):
    """CR 1. The window's extension host maps the old version's native addon
    and names its directory nowhere else. With no agent running, a snapshot
    of cmdline, cwd and exe alone reads that version as idle — and pruning it
    breaks the next session that window starts."""
    _active_extension(host, "2.1.278")
    loaded = _extension(host, "2.1.273")
    _extension(host, "2.1.278")
    live_mmap(loaded / "resources" / "audio-capture" / "x64-linux" / "audio-capture.node")
    run_gc(host, "--prune")
    assert loaded.exists()


def test_a_mapped_file_protects_every_tier(host, live_mmap):
    """The fourth liveness source is not extension-specific: a node tree whose
    native module a running process has mapped is in use, whatever launched it."""
    tree = _fill(host["npx"] / "dddddddddddddddd")
    live_mmap(tree / "node_modules" / "addon.node")
    run_gc(host, "--prune")
    assert tree.exists()


def test_keeps_the_active_extension_with_no_process_live(host):
    """An editor between reloads has no running agent. That is not licence to
    delete the version it will start next."""
    _active_extension(host, "2.1.278")
    idle = _extension(host, "2.1.266")
    active = _extension(host, "2.1.278")
    data = report(host, "--prune")
    assert not idle.exists()
    assert active.exists()
    assert _extension_warnings(data) == []


def _write_manifest(host, case: str) -> None:
    root = host["vscode"] / "extensions"
    manifest = root / "extensions.json"
    if case == "absent":
        return
    if case == "unparseable":
        manifest.write_text('[{"identifier": {"id": "anthropic.claude-code"}, "vers')
    elif case == "names-nothing":
        manifest.write_text("[]")
    elif case == "names-another-extension":
        manifest.write_text(json.dumps([_manifest_entry(root, "ms-python.python", "2025.1.0")]))
    elif case == "names-a-version-not-on-disk":
        _active_extension(host, "2.1.279")
    elif case == "not-a-list":
        # Wraps an entry naming the version on disk, so only refusing the
        # shape passes — not naming nothing.
        manifest.write_text(
            json.dumps({"extensions": [_manifest_entry(root, CLAUDE_EXT, "2.1.278")]})
        )
    elif case == "wrong-shape-after-a-valid-entry":
        # The parser prints the valid name, then crashes: what it printed
        # before the crash is a partial list, not evidence.
        entries = [_manifest_entry(root, CLAUDE_EXT, "2.1.278"), {"identifier": CLAUDE_EXT}]
        manifest.write_text(json.dumps(entries))


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "unparseable",
        "names-nothing",
        "names-another-extension",
        "names-a-version-not-on-disk",
        "not-a-list",
        "wrong-shape-after-a-valid-entry",
    ],
)
def test_no_usable_manifest_removes_no_extension(host, case):
    """The manifest is the only evidence of which version is active, so every
    way of lacking it means remove *nothing*: a half-written manifest explains
    an empty or mismatched one at least as well as a real uninstall does. One
    naming a version that is not on disk is no better — pruning the rest would
    leave the editor nothing to start. And the refusal is reported, or the
    sensor is back to printing `0B` over ~220 MB a version."""
    versions = [_extension(host, "2.1.259"), _extension(host, "2.1.278")]
    _write_manifest(host, case)
    data = report(host, "--prune")
    assert all(v.exists() for v in versions)
    assert data["pruned"] == []
    assert len(_extension_warnings(data)) == 1


def test_no_claude_extension_is_not_a_warning(host):
    """A host without the extension has nothing to evaluate; absence of the
    tier must not read as a broken manifest."""
    assert _extension_warnings(report(host)) == []


def test_other_extensions_are_never_candidates(host):
    """Scoped to the Claude extension on purpose: other publishers' extensions
    are small, and a manifest that omits one is weaker evidence than a match on
    the one extension this tier is about. The version-digit anchor keeps a
    hypothetical sibling id from being read as a Claude version."""
    _active_extension(host, "2.1.278")
    _extension(host, "2.1.278")
    root = host["vscode"] / "extensions"
    other = _fill(root / "ms-python.python-2025.1.0-linux-x64")
    sibling = _fill(root / f"{CLAUDE_EXT}-companion-1.0.0")
    run_gc(host, "--prune")
    assert other.exists()
    assert sibling.exists()


def test_a_fresh_extension_version_is_withheld(host):
    """The grace window covers this tier too: a version VS Code is unpacking
    right now is named by no process and, until it finishes, by no manifest."""
    _active_extension(host, "2.1.278")
    active = _extension(host, "2.1.278")
    stale = time.time() - 7200
    for path in [active, *active.rglob("*")]:
        os.utime(path, (stale, stale))
    fresh = _extension(host, "2.1.281")
    data = report(host, "--prune", DISK_GC_GRACE_MINUTES=60)
    assert fresh.exists()
    assert [c["kind"] for c in data["withheld"]] == ["vscode-extension"]


def test_extensions_root_is_overridable(host, tmp_path):
    elsewhere = tmp_path / "elsewhere-extensions"
    _active_extension(host, "2.1.278", root=elsewhere)
    idle = _fill(elsewhere / f"{CLAUDE_EXT}-2.1.259-linux-x64")
    active = _fill(elsewhere / f"{CLAUDE_EXT}-2.1.278-linux-x64")
    run_gc(host, "--prune", DISK_GC_EXTENSIONS_ROOT=elsewhere)
    assert not idle.exists()
    assert active.exists()


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


@pytest.mark.parametrize("script", ["disk-gc.sh", "slim-ollama-image.sh"])
def test_help_is_derived_from_the_header_not_hardcoded_lines(script):
    """CR 15 and CR 18. Both scripts print `--help` by slicing their own header
    comment. Hardcoded line numbers meant one inserted line silently truncated
    the usage text or leaked the `Pinned by` pointer into it — no error, no
    test. CR 15 fixed one script and CR 18 caught that the other was missed, so
    this is parametrised over both: the recurrence was the fix being applied
    per-file by hand."""
    path = SCRIPT.parent / script
    assert "sed -n '2,/^[^#]/p'" in path.read_text(), "help range is hardcoded again"

    out = subprocess.run([str(path), "--help"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    assert "Usage" in out.stdout
    assert "Pinned by" not in out.stdout
    assert "set -uo pipefail" not in out.stdout


def test_runs_without_a_virtualenv(host):
    """The unit is exempt from the #279 venv guard precisely because this path
    must work when the venv does not. Guard that it never grows a dependency on
    `uv` or the project environment."""
    assert "uv run" not in SCRIPT.read_text()
    env_free = shutil.which("bash")
    assert env_free, "bash is the only interpreter this script may need"
    result = run_gc(host, "--json", VIRTUAL_ENV="", PATH="/usr/local/bin:/usr/bin:/bin")
    assert result.returncode == 0, result.stderr
