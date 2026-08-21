"""Every source that mints a Person must have a PM ``identifier_type`` (#255).

PM requires ``identifier_type`` as a non-null string on a person observation, so a source
that mints Persons without a mapping is not a soft degradation — it is a guaranteed 422 for
every row of that cohort, discovered in the outbox rather than in CI. That is exactly how
#228 shipped ``usa_wa_legislature_roster`` and rejected all 2,494 of its Persons.

**This test reads the minting sites, not a hand-written list.** The first attempt at the
guarantee enumerated the sources in a literal beside the map it was checking, so the
assertion reduced to "a dict's keys are in the dict" — it could not fail for the reason it
existed, and it was wrong on arrival (it named ``usa_wa_pdc``, which has minted no Person
since the #101 demotion to an identifier-only link). Here the domain comes from an AST walk
over every ``Person(source=…)`` construction in the workspace, so a new minting site fails
CI whether or not anyone remembers this file.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from usa_wa_sync_powermap.descriptors.person import identifier_type_for

#: Workspace root — this file is ``packages/<pkg>/tests/<name>.py``.
_ROOT = Path(__file__).resolve().parents[3]


def _module_name(path: Path) -> str:
    """``packages/x/src/pkg/mod.py`` → ``pkg.mod`` (the installed import path)."""
    src = path.parts.index("src")
    dotted = ".".join(path.parts[src + 1 :]).removesuffix(".py")
    return dotted.removesuffix(".__init__")


def _person_source_expressions() -> list[tuple[Path, ast.expr]]:
    """Every ``source=`` argument passed to a ``Person(...)`` construction under ``src``."""
    found: list[tuple[Path, ast.expr]] = []
    for path in sorted(_ROOT.glob("packages/*/src/**/*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "Person":
                continue
            for kw in node.keywords:
                if kw.arg == "source":
                    found.append((path, kw.value))
    return found


def _resolve(path: Path, expr: ast.expr) -> str:
    """The literal source slug an expression denotes, importing the module for a Name."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.Name):
        module = importlib.import_module(_module_name(path))
        return getattr(module, expr.id)
    raise AssertionError(f"{path}: unresolvable Person(source=…) expression {ast.dump(expr)}")


def test_the_scan_finds_the_known_minting_sites():
    """Guards the guard: an AST walk that silently matched nothing would pass everything."""
    sources = {_resolve(p, e) for p, e in _person_source_expressions()}
    assert "usa_wa_legislature_roster" in sources  # roster_pdf/persons.py
    assert len(sources) >= 1


@pytest.mark.parametrize(
    "source", sorted({_resolve(p, e) for p, e in _person_source_expressions()})
)
def test_every_person_minting_source_has_an_identifier_type(source):
    assert identifier_type_for(source) is not None, (
        f"{source!r} mints Persons but has no PM identifier_type — add it to "
        "SOURCE_TO_IDENTIFIER_TYPE, and register the type in PM before producing"
    )
