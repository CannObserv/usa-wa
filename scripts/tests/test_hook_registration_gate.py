"""Tests for the SessionStart hook-registration gate (#263).

A Claude Code hook is TWO artifacts: the script under ``.claude/hooks/`` and an
entry in ``.claude/settings.json`` naming it. Only the second makes it run, and
the first is the only one anybody sees when listing a directory — so "installed,
unregistered, never runs" is invisible by observation. That is the shape #263
was filed for, and the health hook it installed is the worst possible victim:
silent when clean by design, so an unwired copy and a healthy one produce
byte-identical output (gregoryfoster/skills#179).

``.skills/doctor.sh`` detects the state, but its DEFAULT invocation — the one
every ``reviewing-*``/``shipping-*`` Phase 1 runs — only warns and exits 0, so
review preflights are not hard-blocked by a defect outside the diff under review.
``--check-only`` is the deliberate probe that exits 1 on it
(gregoryfoster/skills#231). Nothing ran that mode here, so the detector this repo
just installed could be silently unwired by an editor of settings.json and no
gate would notice.

These tests pin the wiring, not the doctor: the doctor has its own suite
upstream, and asserting its behaviour here would just re-test the vendor.
"""

import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PRECOMMIT = REPO / ".pre-commit-config.yaml"
SETTINGS = REPO / ".claude" / "settings.json"
HOOKS_DIR = REPO / ".claude" / "hooks"


def _local_hook_entries() -> list[dict]:
    config = yaml.safe_load(PRECOMMIT.read_text())
    return [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]


def test_precommit_runs_the_doctor_registration_probe() -> None:
    """The gate exists, and runs the mode that actually fails on a wiring gap.

    Asserting ``--check-only`` specifically is the point: a hook wired to the
    bare ``.skills/doctor.sh`` would print the same warning and exit 0, which is
    a gate that reports damage and then signals "fine" to the thing branching on
    it.
    """
    entries = [h for h in _local_hook_entries() if "doctor.sh" in h["entry"]]
    assert entries, (
        "no local pre-commit hook runs .skills/doctor.sh; an unregistered "
        "SessionStart hook would reach main unnoticed (#263)"
    )
    assert all("--check-only" in h["entry"] for h in entries), (
        "the doctor gate must run --check-only: the default invocation warns "
        "about an unregistered hook and still exits 0 (gregoryfoster/skills#231)"
    )


def test_precommit_doctor_gate_always_runs() -> None:
    """A wiring gap is not carried by any one staged file.

    Scoping the gate with ``files:`` would let a settings.json edit made in a
    commit that touches nothing under .claude/ slip past it — and an edit that
    REMOVES a registration is exactly the commit that would.
    """
    for hook in _local_hook_entries():
        if "doctor.sh" not in hook["entry"]:
            continue
        assert hook.get("always_run") is True, f"{hook['id']}: needs always_run"
        assert hook.get("pass_filenames") is False, (
            f"{hook['id']}: the doctor scans the tree, not a file list"
        )
        assert "files" not in hook, f"{hook['id']}: must not be path-scoped"


def _registered_commands() -> list[str]:
    """Every hook command in settings.json, across all events.

    Not just SessionStart: ``context-budget-guard.sh`` is a PostToolUse hook, and
    a scan narrowed to one event reports a correctly-wired hook as unregistered.
    """
    return [
        hook["command"]
        for groups in json.loads(SETTINGS.read_text())["hooks"].values()
        for group in groups
        for hook in group["hooks"]
    ]


def test_every_installed_hook_is_registered() -> None:
    """Every hook script on disk is named by some hook command.

    The doctor makes the same comparison, but only where its vendored content is
    checked out; a worktree or CI clone with uninitialized submodules gets a
    dangling doctor and no check at all. This runs off two tracked files and so
    holds everywhere the suite does.
    """
    registered = " ".join(_registered_commands())
    installed = sorted(p.name for p in HOOKS_DIR.glob("*.sh"))
    assert installed, "no hooks found — has .claude/hooks/ moved?"

    unregistered = [name for name in installed if name not in registered]
    assert not unregistered, (
        f"installed but never run: {unregistered}. For a vendored hook, re-run "
        f"its installer (see the <hook>.install manifest beside it); for a "
        f"project-local one, add the SessionStart entry to {SETTINGS.name}."
    )


def test_registrations_are_project_dir_anchored() -> None:
    """Commands resolve against the project, not the session's cwd.

    A cwd-relative command silently fails in any session started elsewhere, and
    the hook whose failure is least visible is the one that reports nothing when
    clean. Registrations written before gregoryfoster/skills#110 have this shape;
    re-running the installer upgrades them in place.
    """
    for command in _registered_commands():
        # Commands spelled as a repo-relative path — the shape the installers
        # write, and the only one that can be cwd-relative. An absolute path is
        # cwd-independent already, so it needs no anchor and is not a finding.
        if ".claude/hooks/" not in command or command.startswith("bash /"):
            continue
        assert "CLAUDE_PROJECT_DIR" in command, (
            f"cwd-relative hook command: {command!r} — re-run its installer"
        )
