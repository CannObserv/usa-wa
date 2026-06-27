"""Tests for scripts/verify-units.sh — the pre-commit gate over deploy/ units.

`systemd-analyze verify` has two gaps this wrapper closes (see issue #51):
  * misspelled directives / unknown keys warn on **stderr but exit 0** —
    a plain `$?` gate would pass them, so the wrapper must fail on warnings too;
  * nonexistent ExecStart binaries exit 1 — pass that through unchanged.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "verify-units.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("systemd-analyze") is None,
    reason="systemd-analyze not installed",
)

CLEAN_UNIT = """\
[Unit]
Description=Test unit

[Service]
Type=oneshot
ExecStart=/bin/true
"""


def _run(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
    )


def test_clean_unit_passes(tmp_path):
    unit = tmp_path / "clean.service"
    unit.write_text(CLEAN_UNIT)
    assert _run(unit).returncode == 0


def test_unknown_directive_fails_despite_analyze_exit_zero(tmp_path):
    # `Frobnicate=` warns ("Unknown key name … ignoring") but systemd-analyze
    # exits 0 — the wrapper must still fail.
    unit = tmp_path / "typo.service"
    unit.write_text(CLEAN_UNIT.replace("Description=Test unit", "Frobnicate=1"))
    result = _run(unit)
    assert result.returncode != 0


def test_bad_execstart_binary_fails(tmp_path):
    unit = tmp_path / "badbin.service"
    unit.write_text(CLEAN_UNIT.replace("/bin/true", "/usr/local/bin/nope-xyz-123"))
    assert _run(unit).returncode != 0


def test_one_bad_among_many_fails(tmp_path):
    good = tmp_path / "good.service"
    good.write_text(CLEAN_UNIT)
    bad = tmp_path / "bad.service"
    bad.write_text(CLEAN_UNIT.replace("Description=Test unit", "Frobnicate=1"))
    assert _run(good, bad).returncode != 0
