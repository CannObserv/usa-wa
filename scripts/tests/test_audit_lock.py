"""The lock audit reports and never fails (#499).

``scripts/audit-lock.sh`` runs ``uv audit --frozen`` against ``uv.lock``;
``scripts/pre-ship.sh`` runs it before handing off to the gate. The update
mechanism itself is Dependabot, monthly and grouped (``.github/dependabot.yml``):
this report is what shows a disclosure between its runs.

**Report-only is the contract.** A gate that failed on a finding would block
every unrelated ship on someone else's disclosure date (CannObserv/broker#86,
#79). So every exit is 0. A finding prints a ``WARNING:`` line, and an audit
that could not run gets its own wording, so it never reads as clean.

Ported from broker's ``scripts/audit_lock.sh`` less its GitHub Actions
branches: usa-wa runs no CI workflow that calls it. The script runs here
against a fake ``uv`` on ``PATH``, so nothing reaches OSV.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "audit-lock.sh"  # scripts/tests/ → scripts/


def _run(tmp_path: Path, uv_exit: int, uv_output: str = ""):
    """Run the script with a fake ``uv`` that records its argv, prints
    ``uv_output`` and exits ``uv_exit``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "argv"
    (tmp_path / "output").write_text(uv_output)
    fake = bin_dir / "uv"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" > "{argv_file}"\n'
        f'cat "{tmp_path / "output"}"\n'
        f"exit {uv_exit}\n"
    )
    fake.chmod(0o755)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30
    )
    argv = argv_file.read_text().split() if argv_file.exists() else []
    return result, argv


@pytest.mark.parametrize("uv_exit", [0, 1, 2, 127])
def test_every_exit_is_zero(tmp_path, uv_exit):
    """Whatever uv says, pre-ship carries on: report-only."""
    result, _ = _run(tmp_path, uv_exit)
    assert result.returncode == 0, result.stderr


def test_audits_the_lock_without_relocking(tmp_path):
    """``--frozen`` reads ``uv.lock`` as committed: the report describes the
    lock being shipped, not one re-resolved on the spot."""
    _, argv = _run(tmp_path, 0)
    assert argv[:2] == ["audit", "--frozen"]


def test_names_the_preview_feature_this_uv_knows(tmp_path):
    """``uv audit`` is a preview command. uv 0.11 calls the feature ``audit``;
    broker's ``audit-command`` is an unknown flag here and exits 2, which would
    make every run read "did not run"."""
    _, argv = _run(tmp_path, 0)
    assert argv[argv.index("--preview-features") + 1] == "audit"


def test_clean_lock_raises_no_warning(tmp_path):
    result, _ = _run(tmp_path, 0, "Found no known vulnerabilities\n")
    assert "WARNING" not in result.stdout + result.stderr
    assert "Found no known vulnerabilities" in result.stdout


def test_finding_prints_the_audit_and_a_warning(tmp_path):
    out = "- PYSEC-2026-4175: urllib3: HTTPS proxy TLS configuration\n"
    result, _ = _run(tmp_path, 1, out)
    assert "PYSEC-2026-4175" in result.stdout
    assert "WARNING: uv.lock advisories" in result.stderr


@pytest.mark.parametrize("uv_exit", [2, 127])
def test_an_audit_that_did_not_run_never_reads_as_clean(tmp_path, uv_exit):
    """uv exits 1 for findings and 2 for an error: an outage at OSV, or a
    changed flag on an experimental command. A missing uv exits 127."""
    result, _ = _run(tmp_path, uv_exit)
    assert "WARNING: uv audit did not run" in result.stderr
    assert f"exited {uv_exit}" in result.stderr
    assert "uv.lock advisories" not in result.stderr


def test_the_warning_names_the_remedy_that_moves_a_transitive_package(tmp_path):
    """Dependabot's grouped uv PR moves direct dependencies only
    (dependabot-core#14073): 34 of #499's 43 advisories sat in transitive ones
    (starlette, urllib3, mako, virtualenv). So the warning names
    ``uv lock --upgrade-package`` and never promises the monthly PR carries it."""
    result, _ = _run(tmp_path, 1, "- PYSEC-2026-4175: urllib3\n")
    assert "uv lock --upgrade-package <name>" in result.stderr
    assert "direct dependencies only" in result.stderr
    assert "carry it" not in result.stderr
