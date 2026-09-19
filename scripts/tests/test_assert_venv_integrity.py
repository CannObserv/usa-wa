"""Tests for the shared-venv integrity guard (issue #279).

The #279 root cause: ``/home/exedev/usa-wa`` is both ``usa-wa.service``'s
``WorkingDirectory=`` and the parent of every worktree, and the worktree skill's
default linked the prod ``.venv`` into each new worktree. One ``uv run`` there
re-pointed every editable install in the *shared production venv* at the
worktree's paths. ``.skills/worktree_venv=none`` closes that door (docs/SKILLS.md
§ Worktree venv isolation), but nothing detects the state once a venv is in it.

What makes it worth a guard is the *latency*. The venv is corrupted at
worktree-test time and detonates at the next unit start, which may be days later
— and the error it detonates with names the logging config, not the venv::

    ModuleNotFoundError: No module named 'clearinghouse_core'
    ValueError: Cannot resolve 'clearinghouse_core.logging.build_json_formatter'
    ValueError: Unable to configure formatter 'json'

Neither existing guard covers it: ``--frozen --no-sync`` (#30) stops a *unit
start* from mutating the venv and does nothing about a *worktree* mutating it out
of band, and ``assert-main-checkout.sh`` (#87) guards which branch is checked out,
not which tree the venv points into.

These tests drive the script through fabricated venvs under ``tmp_path`` — no
subprocess touches the real one. ``USA_WA_DEPLOY_ROOT`` is what makes that
possible, and is the same escape hatch ``USA_WA_DEPLOY_BRANCH`` is for
``assert-main-checkout.sh``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "assert-venv-integrity.sh"

#: The shape uv actually writes — a compact record, no spaces. The guard must not
#: depend on that: PEP 610 fixes the field NAMES, not the serializer's whitespace,
#: and AGENTS.md § Project Layout forbids keying a parser on an exact upstream
#: string. `test_whitespace_in_the_record_does_not_hide_a_finding` is the proof.
COMPACT = '{{"url":"file://{path}","dir_info":{{"editable":true}}}}'

#: The same record pretty-printed. Doubled braces are ``str.format`` escapes, not
#: part of the JSON.
PRETTY = """{{
  "url": "file://{path}",
  "dir_info": {{
    "editable": true
  }}
}}"""


def _write_record(site_packages: Path, name: str, source: Path, *, template: str = COMPACT) -> None:
    """Install ``name`` into a fabricated site-packages as editable from ``source``."""
    dist_info = site_packages / f"{name}-0.1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "direct_url.json").write_text(template.format(path=source))


def _venv(root: Path, *, python: str = "python3.12") -> Path:
    site_packages = root / ".venv" / "lib" / python / "site-packages"
    site_packages.mkdir(parents=True)
    return site_packages


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GUARD)],
        env={"PATH": "/usr/bin:/bin", "USA_WA_DEPLOY_ROOT": str(root)},
        capture_output=True,
        text=True,
    )


def test_the_guard_exists_and_is_executable() -> None:
    """Systemd resolves an ExecStartPre= path at unit start; a non-executable one fails it."""
    assert GUARD.is_file()
    assert GUARD.stat().st_mode & 0o111, "guard script must be executable"


def test_a_healthy_venv_passes(tmp_path: Path) -> None:
    site_packages = _venv(tmp_path)
    _write_record(site_packages, "usa_wa_common", tmp_path / "packages" / "usa-wa-common")
    _write_record(site_packages, "clearinghouse_core", tmp_path / "packages" / "clearinghouse-core")

    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_an_install_pointing_at_a_worktree_fails(tmp_path: Path) -> None:
    """The #279 state itself: one editable record restamped to ``.worktrees/…``.

    The case the obvious implementation gets wrong. A worktree lives *inside* the
    checkout, so a guard asking "is this path under the root?" answers **yes** for
    the exact corruption it was written to catch. Only an allowlist — the path is
    ``<root>/packages/<one segment>``, which is what ``uv sync --locked`` restores
    — separates the two.
    """
    site_packages = _venv(tmp_path)
    _write_record(site_packages, "usa_wa_common", tmp_path / "packages" / "usa-wa-common")
    _write_record(
        site_packages,
        "clearinghouse_core",
        tmp_path / ".worktrees" / "feature-x" / "packages" / "clearinghouse-core",
    )

    result = _run(tmp_path)
    assert result.returncode == 1
    # The whole point is that the operator learns the cause here rather than from
    # `Unable to configure formatter 'json'` — so the offending package and its
    # actual source must both be in the message.
    assert "clearinghouse_core" in result.stderr
    assert ".worktrees/feature-x" in result.stderr
    assert "uv sync --locked" in result.stderr, "the message must carry the repair"


def test_a_sibling_directory_is_not_inside_the_root(tmp_path: Path) -> None:
    """``/home/exedev/usa-wa-scratch`` must not pass a check on ``/home/exedev/usa-wa``.

    The other half of the path comparison. A bare string prefix accepts every
    sibling whose name merely *starts* with the root's; the allowlist rejects it
    because the separator after the root is part of the pattern.
    """
    root = tmp_path / "usa-wa"
    sibling = tmp_path / "usa-wa-scratch"
    site_packages = _venv(root)
    _write_record(site_packages, "usa_wa_common", sibling / "packages" / "usa-wa-common")

    result = _run(root)
    assert result.returncode == 1
    assert "usa-wa-scratch" in result.stderr


def test_whitespace_in_the_record_does_not_hide_a_finding(tmp_path: Path) -> None:
    """A pretty-printed ``direct_url.json`` is still read as editable.

    uv writes the compact form today. A guard that matched that spelling
    literally would silently stop finding *any* editable install the day a
    writer added spaces — and "no editable installs" is a state this guard
    reports, so the regression would be visible rather than silent. It is
    cheaper to not have the dependency.
    """
    site_packages = _venv(tmp_path)
    _write_record(
        site_packages,
        "usa_wa_common",
        tmp_path / ".worktrees" / "feature-x" / "packages" / "usa-wa-common",
        template=PRETTY,
    )

    result = _run(tmp_path)
    assert result.returncode == 1
    assert "usa_wa_common" in result.stderr


def test_a_non_editable_record_is_not_a_finding(tmp_path: Path) -> None:
    """Only editable installs carry the worktree hazard.

    A wheel built from a path — ``editable`` absent or false — is copied into the
    venv, so where it came from says nothing about where imports resolve. Treating
    those as findings would fail the guard on ordinary local builds.
    """
    site_packages = _venv(tmp_path)
    _write_record(site_packages, "usa_wa_common", tmp_path / "packages" / "usa-wa-common")
    (site_packages / "vendored_wheel-1.0.dist-info").mkdir()
    (site_packages / "vendored_wheel-1.0.dist-info" / "direct_url.json").write_text(
        '{"url":"file:///tmp/build/vendored-wheel","dir_info":{}}'
    )

    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_a_venv_with_no_editable_installs_fails(tmp_path: Path) -> None:
    """Zero workspace packages is the other way this venv is unusable.

    A unit starting against it raises the same ``ModuleNotFoundError``, so the
    guard reports it rather than passing a venv it proved nothing about — an
    empty scan is the classic way a check of this shape passes for the wrong
    reason.
    """
    _venv(tmp_path)

    result = _run(tmp_path)
    assert result.returncode == 1
    assert "no editable" in result.stderr.lower()


def test_a_missing_venv_fails(tmp_path: Path) -> None:
    """Fail closed: an absent venv is 'not verifiably intact', like #87's detached HEAD."""
    (tmp_path / "packages").mkdir()

    result = _run(tmp_path)
    assert result.returncode == 1
    assert ".venv" in result.stderr


def test_every_interpreter_version_in_the_venv_is_scanned(tmp_path: Path) -> None:
    """``lib/python*/site-packages`` is a glob, not a pinned 3.12.

    A Python upgrade moves the whole directory. A guard hardcoding the old name
    would then scan nothing and pass — the failure mode this suite exists to
    prevent, arriving on the one day the venv is most likely to be rebuilt wrong.
    """
    site_packages = _venv(tmp_path, python="python3.14")
    _write_record(
        site_packages,
        "usa_wa_common",
        tmp_path / ".worktrees" / "feature-x" / "packages" / "usa-wa-common",
    )

    result = _run(tmp_path)
    assert result.returncode == 1
    assert "usa_wa_common" in result.stderr


@pytest.mark.skipif(
    not (Path("/home/exedev/usa-wa") / ".venv").is_dir(),
    reason="no production checkout on this host",
)
def test_the_production_venv_is_intact() -> None:
    """The guard's default root, run for real.

    Every fabricated case above proves the logic; this proves the constant. A
    guard about to become an ``ExecStartPre=`` on thirteen units must be known to
    pass against the venv those units actually start from — and a red here is a
    live finding, not a test defect.
    """
    result = subprocess.run(
        ["bash", str(GUARD)], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
