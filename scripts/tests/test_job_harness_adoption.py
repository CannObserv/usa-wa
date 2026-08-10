"""Every operational CLI runs on the shared job harness, with a unique slug (#179b).

#179a built ``clearinghouse_core.job.run_job()`` and adopted it in one pilot. #179b swept
the rest. Without a guard the sweep decays the same way it arose: the cheapest way to add a
capability was always script #48, and a new entry point that hand-rolls its own
``os.environ`` read, ``ArgumentParser``, engine and exit code costs nothing to write and
silently drops out of the #178 ledger and the ``/api/v1/health/jobs`` read surface.

Three properties, each easy to undo by accident:

1. every ``python -m``-able module under ``packages/*/src`` calls ``run_job()``;
2. each declares a ``JOB_SLUG`` — the ledger's stable identity, so a module can move
   without orphaning its run history — and passes it to ``run_job``;
3. no two jobs share a slug, because ``/health/jobs`` is ``DISTINCT ON (job_slug)`` and a
   collision would silently merge two jobs' histories into one row.

Static AST parse — no imports, no DB, no subprocess. A CLI's ``main`` never runs here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _job_scan import EXEMPT, PACKAGES, REPO
from _job_scan import entry_points as _entry_points
from _job_scan import jobs as _jobs
from _job_scan import relative as _relative


def _calls_run_job(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "run_job")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "run_job")
        )
        for node in ast.walk(tree)
    )


def _job_slug(tree: ast.Module) -> str | None:
    """The module-level ``JOB_SLUG = "..."`` literal, if it declares one."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "JOB_SLUG":
                    return str(node.value.value)
    return None


def test_the_sweep_found_something_to_guard() -> None:
    """A guard over an empty glob passes for the wrong reason. #179 counted ~47."""
    assert len(_jobs()) >= 40, "the entry-point scan found almost nothing"


@pytest.mark.parametrize("path", _jobs(), ids=_relative)
def test_every_entry_point_runs_on_the_job_harness(path: Path) -> None:
    tree = ast.parse(path.read_text())
    assert _calls_run_job(tree), (
        f"{_relative(path)} is a `python -m` entry point that does not call run_job(). "
        "A hand-rolled scaffold drops out of the #178 ledger and /api/v1/health/jobs, and "
        "re-opens the 47-file-edit problem #179 closed. If it is genuinely not a job "
        "(a daemon, not a run), add it to EXEMPT with the reason."
    )


@pytest.mark.parametrize("path", _jobs(), ids=_relative)
def test_every_job_declares_a_stable_slug(path: Path) -> None:
    tree = ast.parse(path.read_text())
    slug = _job_slug(tree)
    assert slug, (
        f"{_relative(path)} calls run_job() without a module-level JOB_SLUG. The slug is "
        "the ledger's stable identity: a literal passed inline moves with the module and "
        "orphans its run history."
    )
    assert slug == slug.strip().lower() and " " not in slug, (
        f"{_relative(path)}'s JOB_SLUG {slug!r} is not a lowercase, space-free identifier"
    )


def test_job_slugs_are_unique() -> None:
    """``/health/jobs`` is ``DISTINCT ON (job_slug)``: a collision merges two jobs' run
    histories into one row, and the staler of the two becomes invisible."""
    by_slug: dict[str, list[str]] = {}
    for path in _jobs():
        slug = _job_slug(ast.parse(path.read_text()))
        if slug:
            by_slug.setdefault(slug, []).append(_relative(path))
    collisions = {slug: paths for slug, paths in by_slug.items() if len(paths) > 1}
    assert not collisions, f"duplicate JOB_SLUGs: {collisions}"


def test_the_exempt_list_names_real_modules() -> None:
    """An exemption for a module that moved is an exemption for nothing — and would let
    its replacement slip past the guard unnoticed."""
    known = {_relative(p) for p in _entry_points()}
    assert EXEMPT <= known, f"EXEMPT names modules that no longer exist: {EXEMPT - known}"


def test_no_package_exposes_a_console_script() -> None:
    """The guard above scans ``packages/*/src`` for ``if __name__ == "__main__"``.

    A ``[project.scripts]`` console entry point is the cheapest way to add job #45 in a
    place none of these assertions can see — it needs no ``__main__`` block, so it would
    ship with no ledger row, no ``JOB_SLUG``, and no collision check (CR #196 finding 52).
    No package declares one today. Adding one should be a deliberate act that trips this
    and extends ``_entry_points()``, not a quiet bypass.
    """
    manifests = [REPO / "pyproject.toml", *sorted(PACKAGES.glob("*/pyproject.toml"))]
    offenders = [
        str(p.relative_to(REPO)) for p in manifests if "[project.scripts]" in p.read_text()
    ]
    assert not offenders, (
        f"{offenders} declare console scripts, which the entry-point scan cannot see. "
        "Teach _entry_points() to read them before adding one."
    )
