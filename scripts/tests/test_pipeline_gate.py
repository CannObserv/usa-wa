"""The dbt pipeline gate is wired into pre-commit (#303).

Like ``test_hook_registration_gate``: the gate script existing is not the gate —
only the ``.pre-commit-config.yaml`` entry makes it run, and an unwired script is
observationally identical to a healthy one until a broken model lands on main.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _local_hooks() -> dict[str, dict]:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())
    return {
        hook["id"]: hook
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
    }


def test_dbt_gate_registered() -> None:
    hooks = _local_hooks()
    assert "dbt-build" in hooks, "dbt-build hook missing from .pre-commit-config.yaml"
    hook = hooks["dbt-build"]
    assert hook["files"].startswith("^packages/usa-wa-pipeline/"), (
        "dbt-build must trigger on pipeline-package changes"
    )


def test_dbt_gate_script_exists_and_executable() -> None:
    script = REPO_ROOT / "scripts" / "dbt-gate.sh"
    assert script.is_file(), "scripts/dbt-gate.sh missing"
    assert script.stat().st_mode & 0o111, "scripts/dbt-gate.sh not executable"
