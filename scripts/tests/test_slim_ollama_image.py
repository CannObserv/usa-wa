"""Tests for scripts/slim-ollama-image.sh — the ollama CPU-only rebuild (#394).

`ollama/ollama:latest` costs **8.06 GB on disk / 3.27 GB of content** to serve
one 274 MB embedding model (`nomic-embed-text`), because the image ships CUDA,
ROCm and MLX runtimes: `cuda_v12` 1.2 G, `cuda_v13` 831 M, `mlx_cuda_v13` 2.0 G,
`vulkan` 47 M — 4.0 GB of accelerator libraries on a VM with no accelerator.
That was 35% of a 25 GB volume, and the largest single item on the host.

Stripping them by hand once is not a fix: `docker pull` restores the fat image,
and SocratiCode's `OLLAMA_IMAGE` constant hardcodes that tag. So the reclaim is
a script (repeatable, guarded, verified) and `disk-gc.sh` reports the regression.

The dangerous parts are what the tests pin: the script destroys and recreates a
container, and deletes an 8 GB image. It must refuse on a host that would be
broken by the strip, must not delete the fat image until the replacement is
serving, and must reproduce the container's port/volume/restart settings exactly
— SocratiCode creates it with a plain `docker run`, so a missed flag is a stack
that silently stops working.

`docker` and `curl` are stubbed; nothing here reaches a real daemon.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "slim-ollama-image.sh"

SLIM_LABEL = "cpu-only-gpu-libs-stripped"

#: Directories the strip removes from the image — every accelerator runtime, and
#: nothing else. Named here so a future edit that widens the blast radius (say,
#: to /usr/lib/ollama outright, taking the CPU backends with it) fails a test
#: rather than a production embed call.
GPU_DIRS = ("cuda_v12", "cuda_v13", "mlx_cuda_v13", "vulkan")

#: The port SocratiCode's startOllama() publishes, restated so the rollback-hint
#: assertion fails loudly if the script and the stack ever disagree about it.
OLLAMA_PORT = 11435


def _docker_stub(tmp_path: Path, *, label: str = "", fail_on: str = "") -> Path:
    """A `docker` stand-in that logs every invocation and answers plausibly.

    `label` is what `image inspect --format` reports for the slim marker;
    `fail_on` is a substring of the full argument line that should exit
    non-zero, to drive the failure paths. A substring rather than a word
    because `docker exec` is used twice for different purposes — the strip and
    the model check — and the failure tests mean only one of them.
    """
    log = tmp_path / "docker.log"
    stub = tmp_path / "docker"
    stub.write_text(
        f"""#!/bin/sh
printf '%s\\n' "$*" >> {log}
case "$*" in
  *"{fail_on}"*) [ -n "{fail_on}" ] && exit 1 ;;
esac
case "$1" in
  image)
    case "$2" in
      inspect)
        # The script inspects twice, for different fields: the slim label
        # (the idempotence check) and the image id (the rollback handle).
        case "$*" in
          *".Id"*) printf 'sha256:fatimage00' ;;
          *) printf '%s' "{label}" ;;
        esac ;;
      *) : ;;
    esac ;;
  ps) printf 'socraticode-ollama' ;;
  exec) printf 'nomic-embed-text:latest  0a109f422b47  274 MB' ;;
  export) printf 'tar-bytes' ;;
  # Drain stdin, as real `docker import` does. Exiting without reading it left
  # the `export` upstream of the pipe to die of SIGPIPE (141), which `pipefail`
  # then reported as a failed rebuild — a race that won in isolation and lost
  # under a full-suite load, making every test of the rebuild path flaky.
  import) cat >/dev/null 2>&1; printf 'sha256:deadbeef' ;;
  *) : ;;
esac
exit 0
"""
    )
    stub.chmod(0o755)
    return stub


def _curl_stub(tmp_path: Path, *, dims: int = 768) -> Path:
    stub = tmp_path / "curl"
    body = '{"embeddings":[[' + ",".join(["0.1"] * dims) + "]]}"
    stub.write_text(f"#!/bin/sh\nprintf '%s' '{body}'\nexit 0\n")
    stub.chmod(0o755)
    return stub


def log_lines(tmp_path: Path) -> list[str]:
    log = tmp_path / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def run_slim(tmp_path, *args, gpu_glob: str | None = None, **env_overrides):
    env = {
        **os.environ,
        "SLIM_DOCKER": str(tmp_path / "docker"),
        "SLIM_CURL": str(tmp_path / "curl"),
        # A path glob that, if it matches anything, means this host has an
        # accelerator and must not be stripped.
        "SLIM_GPU_GLOB": gpu_glob or str(tmp_path / "no-such-device-*"),
        "SLIM_MIN_FREE_BYTES": "0",
        **{k: str(v) for k, v in env_overrides.items()},
    }
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=120
    )


@pytest.fixture
def stubs(tmp_path):
    _curl_stub(tmp_path)
    return tmp_path


# ── refusals ──────────────────────────────────────────────────────────────────


def test_refuses_on_a_host_with_an_accelerator(stubs, tmp_path):
    """The strip is only sound because the libraries are dead weight here. On a
    host with a GPU they are the fast path, and removing them is a silent
    downgrade to CPU inference."""
    _docker_stub(tmp_path)
    device = tmp_path / "nvidia0"
    device.write_text("")
    result = run_slim(tmp_path, gpu_glob=str(tmp_path / "nvidia*"))
    assert result.returncode != 0
    assert "gpu" in (result.stdout + result.stderr).lower()
    assert not any("export" in line for line in log_lines(tmp_path))


def test_refuses_without_headroom_for_the_rebuild(stubs, tmp_path):
    """export|import materialises a second image before the first is removed."""
    _docker_stub(tmp_path)
    result = run_slim(tmp_path, SLIM_MIN_FREE_BYTES=2**62)
    assert result.returncode != 0
    assert "space" in (result.stdout + result.stderr).lower()
    assert not any("export" in line for line in log_lines(tmp_path))


def test_is_idempotent_when_the_image_is_already_slim(stubs, tmp_path):
    _docker_stub(tmp_path, label=SLIM_LABEL)
    result = run_slim(tmp_path)
    assert result.returncode == 0
    assert not any("export" in line for line in log_lines(tmp_path))
    assert "already" in result.stdout.lower()


def test_force_rebuilds_an_already_slim_image(stubs, tmp_path):
    _docker_stub(tmp_path, label=SLIM_LABEL)
    assert run_slim(tmp_path, "--force").returncode == 0
    assert any("export" in line for line in log_lines(tmp_path))


# ── the rebuild ───────────────────────────────────────────────────────────────


def test_strips_every_accelerator_runtime_and_nothing_wider(stubs, tmp_path):
    _docker_stub(tmp_path)
    assert run_slim(tmp_path).returncode == 0
    strip = next(line for line in log_lines(tmp_path) if "rm -rf" in line)
    for directory in GPU_DIRS:
        assert f"/usr/lib/ollama/{directory}" in strip
    # The CPU backends live directly under /usr/lib/ollama; removing the parent
    # would take them too.
    assert "rm -rf /usr/lib/ollama " not in strip + " "


def test_stamps_the_slim_label_the_sensor_looks_for(stubs, tmp_path):
    """disk-gc.sh reads exactly this label to detect a pull that undid the work."""
    _docker_stub(tmp_path)
    assert run_slim(tmp_path).returncode == 0
    imported = next(line for line in log_lines(tmp_path) if line.startswith("import"))
    assert f"dev.usa-wa.slim={SLIM_LABEL}" in imported


def test_preserves_the_runtime_config_the_entrypoint_needs(stubs, tmp_path):
    """`docker import` keeps no image config — ENTRYPOINT/CMD/ENV must be
    restated or the rebuilt image starts nothing."""
    _docker_stub(tmp_path)
    assert run_slim(tmp_path).returncode == 0
    imported = next(line for line in log_lines(tmp_path) if line.startswith("import"))
    assert "/bin/ollama" in imported
    assert "serve" in imported
    assert "OLLAMA_HOST=0.0.0.0:11434" in imported


def test_recreates_the_container_exactly_as_socraticode_would(stubs, tmp_path):
    """SocratiCode's startOllama() creates it with a plain `docker run`; the
    replacement must match, or its next start diverges from this one."""
    _docker_stub(tmp_path)
    assert run_slim(tmp_path).returncode == 0
    run = next(line for line in log_lines(tmp_path) if line.startswith("run "))
    assert "--name socraticode-ollama" in run
    assert "-p 11435:11434" in run
    assert "-v socraticode_ollama_data:/root/.ollama" in run
    assert "--restart unless-stopped" in run
    assert "ollama/ollama:latest" in run


def test_keeps_the_tag_socraticode_hardcodes(stubs, tmp_path):
    """OLLAMA_IMAGE is `ollama/ollama:latest` in constants.ts, and
    isOllamaImagePresent() gates the pull — so the slim build must carry that
    tag locally, or the next start re-downloads 8 GB."""
    _docker_stub(tmp_path)
    assert run_slim(tmp_path).returncode == 0
    assert any(
        line.startswith("tag ") and line.endswith("ollama/ollama:latest")
        for line in log_lines(tmp_path)
    )


# ── ordering: the fat image is the rollback ───────────────────────────────────


def test_removes_the_fat_image_only_after_the_replacement_verifies(stubs, tmp_path):
    _docker_stub(tmp_path)
    assert run_slim(tmp_path).returncode == 0
    lines = log_lines(tmp_path)
    removal = next(i for i, line in enumerate(lines) if line.startswith("image rm"))
    started = next(i for i, line in enumerate(lines) if line.startswith("run "))
    # `exec` is used twice — the strip, then the model check. The ordering that
    # matters is against the second one, so match on what it runs.
    verified = next(i for i, line in enumerate(lines) if "ollama list" in line)
    assert started < removal, "container must be running before the old image goes"
    assert verified < removal, "model must be verified before the old image goes"


def test_keeps_the_fat_image_when_verification_fails(stubs, tmp_path):
    """Without the old image there is nothing to roll back to."""
    _curl_stub(tmp_path, dims=1)  # wrong dimensionality = a broken embedder
    _docker_stub(tmp_path)
    result = run_slim(tmp_path)
    assert result.returncode != 0
    assert not any(line.startswith("image rm") for line in log_lines(tmp_path))


def test_a_verification_failure_names_the_rollback_command(stubs, tmp_path):
    """CR 5. Retaining the fat image is the entire rollback strategy, and by
    this point the tag has already moved to the slim build — so "the original
    image is retained" is not on its own something an operator can act on."""
    _curl_stub(tmp_path, dims=1)
    _docker_stub(tmp_path)
    result = run_slim(tmp_path)
    assert result.returncode != 0
    assert "tag sha256:fatimage00 ollama/ollama:latest" in result.stderr
    assert f"-p {OLLAMA_PORT}:11434" in result.stderr
    assert "-v socraticode_ollama_data:/root/.ollama" in result.stderr


def test_an_unreadable_image_id_does_not_print_a_broken_command(stubs, tmp_path):
    """CR 13. Interpolating an empty OLD_ID emitted `docker tag  <image>` — a
    command with an argument silently missing, which is worse than printing
    nothing because it still looks pasteable to an operator mid-incident."""
    log = tmp_path / "docker.log"
    stub = tmp_path / "docker"
    stub.write_text(
        f"""#!/bin/sh
printf '%s\n' "$*" >> {log}
case "$*" in
  *".Id"*) exit 1 ;;
  *"ollama list"*) exit 1 ;;
  image*) printf '' ;;
  import) cat >/dev/null 2>&1 ;;
  *) : ;;
esac
exit 0
"""
    )
    stub.chmod(0o755)
    result = run_slim(tmp_path)
    assert result.returncode != 0
    assert "tag  " not in result.stderr, "an empty id was interpolated into a command"
    assert "could not be read" in result.stderr
    assert "images" in result.stderr


def test_an_explicitly_empty_gpu_glob_disables_the_check(stubs, tmp_path):
    """CR 9. `DISK_GC_DOCKER=''` disables its check rather than crashing; the
    glob must behave the same way instead of tripping `set -u`."""
    _docker_stub(tmp_path)
    result = run_slim(tmp_path, SLIM_GPU_GLOB="")
    assert result.returncode == 0, result.stderr
    assert "unbound" not in result.stderr


def test_keeps_the_fat_image_when_the_model_is_missing(stubs, tmp_path):
    """Fails the model check specifically, not the earlier strip — the container
    is running on the slim image by then, so this is the path where a premature
    `image rm` would leave nothing to roll back to."""
    _docker_stub(tmp_path, fail_on="ollama list")
    result = run_slim(tmp_path)
    assert result.returncode != 0
    assert any(line.startswith("run ") for line in log_lines(tmp_path))
    assert not any(line.startswith("image rm") for line in log_lines(tmp_path))


# ── contract ──────────────────────────────────────────────────────────────────


def test_script_is_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_agrees_with_the_sensor_about_the_label():
    """One label, two readers — a drifting pair would make the sensor report a
    healthy image as fat forever, or never report a fat one at all."""
    assert SLIM_LABEL in SCRIPT.read_text()
    assert SLIM_LABEL in (SCRIPT.parent / "disk-gc.sh").read_text()
