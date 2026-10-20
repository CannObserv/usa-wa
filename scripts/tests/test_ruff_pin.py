"""The two ruffs agree: the pre-commit hook's and the venv's (#504).

Two gates run ruff, and they do not run the same one. The pre-commit hook
(``.pre-commit-config.yaml``) installs ``ruff-pre-commit`` at its pinned ``rev``;
``scripts/pre-ship.sh`` runs ``uv run ruff``, the version in ``uv.lock``. The
monthly Dependabot PR moves the lock only (``.github/dependabot.yml``: ``uv``,
nothing else), so every ruff bump it lands opens a gap the hook never sees. In
the gap the gates disagree: one formats what the other rejects, and a commit the
hook passed fails pre-ship. #504's first monthly PR was exactly that, 0.15.14 in
the hook and 0.17.0 in the lock, with 0.17 newly formatting Markdown.

* **The hook's rev is the locked version.** So the month's PR goes red here
  until the hook is bumped beside it, by hand, in the same PR.
* **Markdown stays out of the formatter.** ruff 0.17 added ``*.md`` to its
  default ``include`` and ``markdown`` to the hook's ``types_or``: it reformats
  Python code fences in docs. Those fences are illustrative, often pseudo-code
  with hand-aligned comments in dated specs, and a dependency bump is no place to
  rewrite them. Excluding them keeps 0.15's behaviour; whether docs should be
  formatted is a decision of its own.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
PRE_COMMIT = REPO / ".pre-commit-config.yaml"
LOCK = REPO / "uv.lock"
PYPROJECT = REPO / "pyproject.toml"
HOOK_REPO = "https://github.com/astral-sh/ruff-pre-commit"


def _hook_rev() -> str:
    repos = yaml.safe_load(PRE_COMMIT.read_text())["repos"]
    revs = [r["rev"] for r in repos if r["repo"] == HOOK_REPO]
    assert len(revs) == 1, f"expected one {HOOK_REPO} entry, found {len(revs)}"
    return revs[0]


def _locked_ruff() -> str:
    packages = tomllib.loads(LOCK.read_text())["package"]
    versions = [p["version"] for p in packages if p["name"] == "ruff"]
    assert len(versions) == 1, f"expected one locked ruff, found {versions}"
    return versions[0]


def test_hook_rev_matches_locked_ruff():
    assert _hook_rev() == f"v{_locked_ruff()}", (
        f"pre-commit runs ruff {_hook_rev()}, pre-ship runs {_locked_ruff()} "
        f"(uv.lock): set {PRE_COMMIT.name}'s ruff-pre-commit rev to v{_locked_ruff()}"
    )


def test_markdown_is_excluded_from_format():
    fmt = tomllib.loads(PYPROJECT.read_text())["tool"]["ruff"].get("format", {})
    assert "*.md" in fmt.get("exclude", []), (
        "ruff >= 0.17 formats Python fences in Markdown; [tool.ruff.format] "
        "exclude must list '*.md'"
    )
