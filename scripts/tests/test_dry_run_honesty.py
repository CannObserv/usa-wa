"""Every job's ``--dry-run`` must mean what the harness says it means (CR #196).

``run_job`` adds ``--dry-run`` to every job with the help text *"Run the work but roll
back instead of committing"*. A job only delivers on that if one of three things is true:

1. it declines the flag (``dry_run=False``) — nothing to promise;
2. the **harness** owns the commit (``commit=True`` and ``needs_db=True``), so
   ``_execute`` rolls the session back for it; or
3. the handler **reads** the flag itself and acts on it.

A ``commit=False`` job that never reads ``ctx.dry_run`` satisfies none of them: it owns a
transaction it commits unconditionally, so ``--dry-run`` runs the work, writes it, and
prints ``dry_run=true``. That was live on the WSL refresh and the PM subscription bootstrap
(finding 47), and — because the first sweep for it grepped for the *token* ``dry_run``
appearing rather than being **consulted**, and both of these mention ``--dry-run`` in a
docstring — on the SOS and PDC refreshes too (finding 55), which run on timers.

Hence this guard, and hence AST rather than text: a docstring that says ``--dry-run`` is
not a job that reads it.

Static parse — no imports, no DB, no subprocess. A CLI's ``main`` never runs here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
PACKAGES = REPO / "packages"

#: Jobs that accept the flag, read nothing, and roll nothing back — because they **write
#: nothing**. Vacuous rather than dishonest: there is no work to undo, so "roll back
#: instead of committing" costs an operator nothing but a shrug (finding 58). Listed
#: explicitly so a job that *starts* writing has to come off this list deliberately.
#:
#: The alternative for any entry here is ``run_job(..., dry_run=False)``, which removes
#: the flag outright. That is the better answer the day one of these grows a write.
READ_ONLY = {
    "usa_wa_adapter_legislature/committees/lineage_invariants.py": "daily invariant check",
    "usa_wa_adapter_legislature/committees/lineage_suggest.py": "advisory; suggestions only",
    "usa_wa_adapter_legislature/committees/probe_extent.py": "needs_db=False WSL probe",
    "usa_wa_adapter_legislature/operators/invariants.py": "read-only succession check",
    "usa_wa_adapter_legislature/sponsors/probe_identity.py": "needs_db=False WSL probe",
    "usa_wa_facts_seats/house_corroboration.py": "report-only unless --strict",
    "usa_wa_sync_powermap/validate_committees.py": "read-only against both sides",
}


def _relative(path: Path) -> str:
    parts = path.parts
    return "/".join(parts[parts.index("src") + 1 :])


def _jobs() -> list[Path]:
    """Every ``python -m`` entry point that calls ``run_job``."""
    return sorted(
        path
        for path in PACKAGES.glob("*/src/**/*.py")
        if 'if __name__ == "__main__"' in path.read_text() and "run_job(" in path.read_text()
    )


def _run_job_keyword(tree: ast.Module, name: str) -> str | None:
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


def _consults_dry_run(tree: ast.Module) -> bool:
    """Whether the module reads ``.dry_run`` off anything — ``ctx``, ``args``, a local.

    An :class:`ast.Attribute` load, so a docstring mentioning ``--dry-run`` does not count.
    That distinction is the whole point: it is what the first pass got wrong.
    """
    return any(
        isinstance(node, ast.Attribute)
        and node.attr == "dry_run"
        and isinstance(node.ctx, ast.Load)
        for node in ast.walk(tree)
    )


def _honesty(path: Path) -> tuple[str, str]:
    """``(verdict, why)`` for one job. ``verdict`` is the mechanism that makes its
    ``--dry-run`` truthful, or ``"unhonoured"``."""
    tree = ast.parse(path.read_text())
    if _run_job_keyword(tree, "dry_run") == "False":
        return "declined", "the flag is not offered"
    commit = _run_job_keyword(tree, "commit") or "True"
    needs_db = _run_job_keyword(tree, "needs_db") or "True"
    if commit == "True" and needs_db == "True":
        return "harness", "the harness owns the commit and rolls it back"
    if _consults_dry_run(tree):
        return "handler", "the handler reads the flag"
    return "unhonoured", f"commit={commit}, needs_db={needs_db}, and nothing reads the flag"


def test_the_scan_found_the_fleet() -> None:
    """A guard over an empty glob passes for the wrong reason."""
    assert len(_jobs()) >= 40, "the entry-point scan found almost nothing"


@pytest.mark.parametrize("path", _jobs(), ids=_relative)
def test_every_job_honours_its_dry_run_flag(path: Path) -> None:
    verdict, why = _honesty(path)
    module = _relative(path)
    if verdict == "unhonoured":
        assert module in READ_ONLY, (
            f"{module} accepts --dry-run ({why}), so the flag promises a rollback it will "
            "not perform: the run writes, commits, and prints dry_run=true. Either pass "
            "dry_run=False to decline the flag, read ctx.dry_run in the handler, or — if "
            "the job genuinely writes nothing — add it to READ_ONLY with the reason."
        )


def test_the_read_only_list_names_real_jobs() -> None:
    """An entry for a module that moved exempts nothing, and would let its replacement
    through unnoticed."""
    known = {_relative(p) for p in _jobs()}
    assert set(READ_ONLY) <= known, (
        f"READ_ONLY names jobs that no longer exist: {set(READ_ONLY) - known}"
    )


def test_the_read_only_list_has_not_absorbed_a_writer() -> None:
    """The list is for jobs with nothing to roll back. If one is now on the harness's own
    commit, or reads the flag, it has outgrown the exemption and should leave the list."""
    stale = {
        module: _honesty(path)[0]
        for path in _jobs()
        if (module := _relative(path)) in READ_ONLY and _honesty(path)[0] != "unhonoured"
    }
    assert not stale, f"READ_ONLY entries that no longer need the exemption: {stale}"
