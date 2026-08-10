"""One definition of "the fleet of jobs", shared by the harness fitness functions.

``test_job_harness_adoption`` and ``test_dry_run_honesty`` each grew their own glob for
"every ``python -m`` entry point", and they agreed — 44 modules apiece — only by
convention (CR #196 finding 63). Nothing coupled them, so a job that stopped calling
``run_job`` would have gone red in the adoption guard while *silently* dropping out of
the dry-run guard: a job leaving a safety net at the moment it most needs one.

Static parse throughout — no imports, no DB, no subprocess. A CLI's ``main`` never runs.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).parent.parent.parent
PACKAGES = REPO / "packages"

#: Entry points that are deliberately **not** jobs. A job runs, reports and exits; these
#: do not, so ``run_job``'s scaffold (one transaction, one terminal outcome, one ledger
#: row) does not describe them.
EXEMPT = {
    # The sidecar daemon: `run_forever()`, no terminal outcome to record. Its health is
    # the #85 failure-streak alerting and the systemd unit's own liveness, not a
    # job_runs row.
    "usa_wa_sync_powermap/__main__.py",
}


def entry_points() -> list[Path]:
    """Every module under ``packages/*/src`` with an ``if __name__ == "__main__"`` block.

    Includes the exempt ones — the adoption guard needs the full set to check that its
    exemptions still name modules that exist.
    """
    return sorted(
        path
        for path in PACKAGES.glob("*/src/**/*.py")
        if 'if __name__ == "__main__"' in path.read_text()
    )


def jobs() -> list[Path]:
    """Every entry point that is a job: the scan minus the deliberate exemptions."""
    return [path for path in entry_points() if relative(path) not in EXEMPT]


def relative(path: Path) -> str:
    """``usa_wa_sync_powermap/__main__.py`` — the package-relative module path."""
    parts = path.parts
    return "/".join(parts[parts.index("src") + 1 :])


def run_job_keyword(tree: ast.Module, name: str) -> str | None:
    """The literal source of ``run_job``'s ``name=`` argument, if it passes one."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if called != "run_job":
            continue
        for keyword in node.keywords:
            if keyword.arg == name:
                return ast.unparse(keyword.value)
    return None
