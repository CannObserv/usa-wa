"""The context manifest must name every doc, and say so when it doesn't (#300).

#263 wired a daily health check for a *declared-but-unindexed* context artifact.
#298 hit the same class of failure from the other side: eleven
``docs/MODULES-*.md`` references, plus ``ARCHITECTURE.md``, ``ONTOLOGY.md`` and
others, existed and were perfectly indexable but were never **declared** in
``.socraticodecontextartifacts.json`` — so ``codebase_context_search`` could not
reach them. Nothing could report that, because the health check only inspects
what the manifest already names. A doc absent from the manifest was invisible to
it, and stayed invisible: the manifest sat at 7 artifacts describing a
"four-layer" repo while the layering had been six layers since #189.

Undeclared is the drift that grows silently — a doc gets added and nobody
updates the manifest. So the comparison runs the other way here: every tracked
Markdown file at the repo root or under ``docs/`` must be covered by some
declared path, or listed in ``.skills/context-artifacts-exempt``.

The exemption list is checked too. An opt-out nobody revisits is a blindfold,
and the two ways it rots — the file is gone, or it has since been declared —
both leave an entry that suppresses nothing while looking like policy.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECKER = REPO / "scripts" / "context_manifest_drift.py"

GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _scratch(tmp_path: Path, *, artifacts: list[str], docs: list[str], exempt: str | None = None):
    """A repo with a manifest, some docs, and (optionally) an exemption list."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=GIT_ENV)
    (tmp_path / ".socraticodecontextartifacts.json").write_text(
        json.dumps({"artifacts": [{"name": p, "path": p, "description": ""} for p in artifacts]})
    )
    for rel in docs:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# doc\n")
    if exempt is not None:
        (tmp_path / ".skills").mkdir(exist_ok=True)
        (tmp_path / ".skills" / "context-artifacts-exempt").write_text(exempt)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, env=GIT_ENV)
    subprocess.run(
        ["git", "commit", "-qm", "seed", "--no-verify"], cwd=tmp_path, check=True, env=GIT_ENV
    )
    return tmp_path


def _run(project_root: Path):
    return subprocess.run(
        [sys.executable, str(CHECKER), "--project-root", str(project_root)],
        capture_output=True,
        text=True,
    )


def test_a_fully_declared_tree_is_silent(tmp_path):
    """Silent when clean — this runs at session start, where noise gets tuned out."""
    repo = _scratch(
        tmp_path,
        artifacts=["./AGENTS.md", "./docs/ARCHITECTURE.md"],
        docs=["AGENTS.md", "docs/ARCHITECTURE.md"],
    )

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == ""


def test_an_undeclared_doc_is_reported(tmp_path):
    """The #298 shape: the doc exists, is indexable, and nothing names it."""
    repo = _scratch(
        tmp_path,
        artifacts=["./AGENTS.md"],
        docs=["AGENTS.md", "docs/MODULES-SPANS.md"],
    )

    result = _run(repo)

    assert result.returncode == 1
    assert "docs/MODULES-SPANS.md" in result.stdout


def test_a_declared_directory_covers_the_files_under_it(tmp_path):
    """`docs/specs` is declared as a directory artifact; its members are covered."""
    repo = _scratch(
        tmp_path,
        artifacts=["./AGENTS.md", "./docs/specs"],
        docs=["AGENTS.md", "docs/specs/2026-01-01-thing.md"],
    )

    result = _run(repo)

    assert result.returncode == 0, result.stdout


def test_an_exempt_doc_is_not_reported(tmp_path):
    """Deliberate omissions need an opt-out, or the check goes noisy and gets muted."""
    repo = _scratch(
        tmp_path,
        artifacts=["./AGENTS.md"],
        docs=["AGENTS.md", "CLAUDE.md"],
        exempt="# a one-line include of AGENTS.md\nCLAUDE.md\n",
    )

    result = _run(repo)

    assert result.returncode == 0, result.stdout


def test_an_exemption_outside_the_checked_scope_says_so(tmp_path):
    """ "Not tracked" would be false, and would send the reader to delete a file.

    Package README.md files are module documentation, not context artifacts, so
    they are out of scope — but they very much exist.
    """
    repo = _scratch(
        tmp_path,
        artifacts=["./AGENTS.md"],
        docs=["AGENTS.md", "packages/pkg/README.md"],
        exempt="packages/pkg/README.md\n",
    )

    result = _run(repo)

    assert result.returncode == 1
    assert "outside the checked scope" in result.stdout
    assert "not a tracked" not in result.stdout


def test_an_exemption_for_a_deleted_file_is_reported(tmp_path):
    """A stale opt-out suppresses nothing and reads like a considered decision."""
    repo = _scratch(
        tmp_path,
        artifacts=["./AGENTS.md"],
        docs=["AGENTS.md"],
        exempt="docs/GONE.md\n",
    )

    result = _run(repo)

    assert result.returncode == 1
    assert "docs/GONE.md" in result.stdout


def test_an_exemption_for_a_declared_file_is_reported(tmp_path):
    """Exempt AND declared is a contradiction; one of the two is out of date."""
    repo = _scratch(
        tmp_path,
        artifacts=["./AGENTS.md", "./docs/API.md"],
        docs=["AGENTS.md", "docs/API.md"],
        exempt="docs/API.md\n",
    )

    result = _run(repo)

    assert result.returncode == 1
    assert "docs/API.md" in result.stdout


def test_untracked_files_are_not_reported(tmp_path):
    """Scratch notes and worktree debris are not the repo's context surface."""
    repo = _scratch(tmp_path, artifacts=["./AGENTS.md"], docs=["AGENTS.md"])
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "scratch.md").write_text("# not committed\n")

    result = _run(repo)

    assert result.returncode == 0, result.stdout


def test_a_missing_manifest_is_a_skip_not_a_finding(tmp_path):
    """A repo that never ran init-socraticode has no drift to report."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=GIT_ENV)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_this_repos_manifest_covers_its_own_docs():
    """The live gate. #298 landed the declarations; this keeps them landed.

    A daily reporter catches drift that arrives without the suite running; this
    catches it at the commit that introduces it, which is the cheaper moment.
    """
    result = _run(REPO)

    assert result.returncode == 0, result.stdout


def test_the_daily_hook_invokes_this_checker():
    """The hook is cadence only; the comparison is the module the gate above runs.

    Two copies of one comparison would drift apart, and the copy that drifted
    would be the one nobody runs interactively. `test_hook_registration_gate.py`
    already proves the hook is wired into settings.json; this proves the wire
    reaches something real.
    """
    hook = REPO / ".claude" / "hooks" / "context-manifest-drift.sh"
    assert hook.is_file(), "the daily reporter is missing"
    assert "scripts/context_manifest_drift.py" in hook.read_text(), (
        "the hook no longer calls the checker the suite gates on"
    )
