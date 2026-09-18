"""Fitness test for the declared-not-implemented tier (#182).

Some mapped tables are *declared* — designed, migrated and tested, but not yet
wired to any source. Nothing in the code distinguished "no writer because it is
waiting on #28" from "no writer because it was abandoned". #182 chose to keep the
tables and make the status explicit instead, via inert markers on the model
modules and classes.

A table can also arrive here from the other direction. ``retired`` (#314) means
it HAD a producer and the producer went: the PM mirrors — ``OrganizationName``,
``OrganizationAcronym``, ``EntityEvent``, ``RoleType`` — were written only by the
sync sidecar, and deleting it left four tables holding real rows that nothing
maintains. That is not the same claim as ``declared`` and must not borrow its
word: a declared table is waiting to be built, a retired one is waiting to be
dropped, and an operator reading "declared" on a table full of stale PM data
would draw the wrong conclusion about whether to trust it. Both statuses carry
the same obligations below — a rationale, an open tracking issue, and a marker
that has to come off if anything ever references the class again.

**The retired tier is empty as of #314 step C**, which dropped all four of those
tables — the status did its job and reached its end state. It is kept rather
than deleted because the distinction above is the part worth keeping: the next
table to lose its producer needs a word that is not ``declared``, and rederiving
that argument is more expensive than leaving the vocabulary standing.

The markers themselves:

- module level, for a module whose every mapped class is declared::

      IMPLEMENTATION_STATUS = "declared"
      IMPLEMENTATION_TRACKING_ISSUES = (28,)
      IMPLEMENTATION_RATIONALE = "one line — why nothing writes this yet"

- class level, for a declared class inside an otherwise-live module::

      class OrganizationIdentifier(Base, TimestampMixin):
          __implementation_status__ = "declared"
          __implementation_tracking_issues__ = (194,)
          __implementation_rationale__ = "one line"

The markers are plain data — no import, no runtime behaviour — so a model module
never depends on this machinery.

This test pins the markers to reality. It derives the orphan set from the source
tree (AST, so comments and docstrings do not count as usage) and asserts the two
sets agree in both directions. A class counts as produced when a non-model module
names it *or* when anything under ``packages/*/src`` — its own module included —
constructs or queries it; the second half is what keeps a *colocated writer* like
``clearinghouse_core.runs`` from reading as an orphan. The assertions:

- a mapped class no live module references must carry a marker — a newly orphaned
  table fails the suite instead of drifting silently;
- a marked class no longer orphaned must lose its marker — the tier cannot go stale
  once someone finally wires it;
- every marker must carry a rationale and name at least one issue from
  ``OPEN_TRACKING_ISSUES`` — a declared table nobody is tracking is just a TODO;
- every wholly declared module must be omitted from the coverage gate, so the
  headline coverage number describes code that actually runs.
"""

import ast
import functools
import pathlib
import tomllib

import pytest
from sqlalchemy.orm import DeclarativeBase

import clearinghouse_core.jurisdictions  # noqa: F401  (registers core models)
import clearinghouse_core.provenance  # noqa: F401
import clearinghouse_core.sweep_state  # noqa: F401
import clearinghouse_domain_legislative  # noqa: F401  (registers every domain model)
from clearinghouse_core.models import Base

DECLARED = "declared"

#: Had a producer; the producer retired and the table is waiting to be dropped
#: (#314). Distinct from DECLARED, which never had one — see the module docstring.
RETIRED = "retired"

#: Every status a marker may carry. Anything else is a typo, and
#: `test_every_declared_marker_names_an_open_tracking_issue` says so by name.
MARKER_STATUSES = frozenset({DECLARED, RETIRED})

#: Tracking issues a declared marker may name. Every entry was verified open on
#: 2026-08-07; closing one without wiring its tables should be a deliberate act,
#: so removing it here is what forces the marker to be revisited.
OPEN_TRACKING_ISSUES = {
    28: "P1c: WSL bill cluster (bills, actions, sponsorships, versions) + discover(since)",
    67: "WSL committee activity + legislation-detail cluster",
    194: "Declared tier: 12 tables with no producer and no implementation issue",
    314: "PM sync cutover: retire the sidecar, its schema and the pm_* anchors",
}

#: Packages excluded from the producer scan.
#:
#: Empty since #314. Its one entry was ``powermap-client``, a generated OpenAPI
#: client whose model names collided with ours (its own ``Person``,
#: ``Assignment``, ``EntityEvent``), so counting it would have made orphaned
#: tables look produced by name alone. Kept as a named, empty set: the next
#: vendored package with colliding model names must exclude itself here
#: deliberately rather than quietly mask an orphan.
EXCLUDED_PACKAGES: frozenset[str] = frozenset()

#: Mapped classes that never reach a production table and so are exempt.
#: Empty since #314 — ``clearinghouse_sync_powermap.testing.FakeEntity`` was
#: sync-engine test scaffolding that entered ``Base.metadata`` only under test.
EXEMPT_CLASSES: frozenset[str] = frozenset()

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _mapped_classes() -> dict[str, type[DeclarativeBase]]:
    """First-party SQLAlchemy models registered on the shared ``Base``, keyed by dotted path.

    ``Base.registry`` is process-global, so whichever test modules ran first may have
    added their own throwaway models to it (``test_adapter_runner.FakeWidget``). Only
    classes defined under a ``packages/*/src`` root are real tables, and filtering on
    that keeps the result independent of collection order.
    """
    first_party = _source_modules()
    out = {}
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if cls.__module__ not in first_party:
            continue
        path = f"{cls.__module__}.{cls.__qualname__}"
        if path not in EXEMPT_CLASSES:
            out[path] = cls
    return out


@functools.cache
def _source_modules() -> frozenset[str]:
    """Dotted module names of every first-party source file."""
    return frozenset(_module_name(path) for path in _source_files())


@functools.cache
def _source_files() -> tuple[pathlib.Path, ...]:
    """Every first-party source file under ``packages/*/src``."""
    return tuple(
        path
        for src in sorted(REPO_ROOT.glob("packages/*/src"))
        if src.parent.name not in EXCLUDED_PACKAGES
        for path in sorted(src.rglob("*.py"))
    )


def _referenced_names(path: pathlib.Path) -> set[str]:
    """Identifiers a module actually *uses* — AST only, so prose mentions do not count."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.name.rsplit(".", 1)[-1])
            if node.asname:
                names.add(node.asname)
    return names


def _used_names(path: pathlib.Path) -> set[str]:
    """Identifiers a module uses *as a class* — constructed, or handed to a query builder.

    Narrower than :func:`_referenced_names` on purpose. It counts only the two shapes a
    producer actually has — ``JobRun(...)`` and ``select(JobRun)`` / ``delete(JobRun)`` —
    and so distinguishes writing a table from the bare ``class JobRun(Base)`` that declares
    it. That is what lets a model module count as its own producer under the *colocated
    writer* pattern, where a module holds both the mapped class and the functions that
    write it (``clearinghouse_core.runs``: ``open_run`` / ``close_run`` / ``record_run``
    live beside ``JobRun``, and ``clearinghouse_core.job`` imports the functions rather
    than the class, so no other module ever names it).

    Type annotations and ``relationship()`` targets are deliberately *not* counted: a
    declared table may still sit in the FK graph of a live one (``JurisdictionRelationship``
    points at ``Jurisdiction``), and naming a class in an annotation writes no rows.
    """
    used: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if not isinstance(node, ast.Call):
            continue
        for expr in (node.func, *node.args):
            if isinstance(expr, ast.Name):
                used.add(expr.id)
            elif isinstance(expr, ast.Attribute):
                used.add(expr.attr)
    return used


def _model_module_files() -> set[pathlib.Path]:
    """Files that *define* mapped classes — declaration is not production.

    Built from the whole registry, exempt classes included: ``testing.py`` declares
    ``FakeEntity`` and is no more a producer than any other model module.
    """
    first_party = _source_modules()
    modules = {
        mapper.class_.__module__
        for mapper in Base.registry.mappers
        if mapper.class_.__module__ in first_party
    }
    return {path for path in _source_files() if _module_name(path) in modules}


def _module_name(path: pathlib.Path) -> str:
    """Dotted module name for a file under a ``packages/*/src`` root."""
    for src in REPO_ROOT.glob("packages/*/src"):
        if src in path.parents:
            rel = path.relative_to(src).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts.pop()
            return ".".join(parts)
    raise AssertionError(f"{path} is not under a packages/*/src root")


def _produced_names() -> set[str]:
    """Class names some module writes or queries — the union of two signals.

    - **Any** reference from a module that is not a model-definition module. A model
      module mentioning its own siblings is declaration, not production; the producers
      are the adapters, the sidecar, the API and the framework services.
    - **Instantiation or query use anywhere** under ``packages/*/src``, the class's own
      module included. Reference-from-elsewhere alone misses the colocated-writer
      pattern (see :func:`_used_names`) and reported the live ``JobRun`` as an orphan.
    """
    model_files = _model_module_files()
    produced: set[str] = set()
    for path in _source_files():
        produced |= _used_names(path)
        if path not in model_files:
            produced |= _referenced_names(path)
    return produced


def _marker(cls: type[DeclarativeBase]) -> dict[str, object] | None:
    """Resolve ``cls``'s declared-tier marker: class level wins, else module level."""
    module = __import__(cls.__module__, fromlist=["__name__"])
    if "__implementation_status__" in cls.__dict__:
        return {
            "scope": "class",
            "status": cls.__dict__["__implementation_status__"],
            "issues": cls.__dict__.get("__implementation_tracking_issues__"),
            "rationale": cls.__dict__.get("__implementation_rationale__"),
        }
    if hasattr(module, "IMPLEMENTATION_STATUS"):
        return {
            "scope": "module",
            "status": module.IMPLEMENTATION_STATUS,
            "issues": getattr(module, "IMPLEMENTATION_TRACKING_ISSUES", None),
            "rationale": getattr(module, "IMPLEMENTATION_RATIONALE", None),
        }
    return None


@pytest.fixture(scope="module")
def classes() -> dict[str, type[DeclarativeBase]]:
    return _mapped_classes()


@pytest.fixture(scope="module")
def produced() -> set[str]:
    return _produced_names()


def test_every_unproduced_model_carries_a_marker(classes, produced):
    """No producer + no marker = an orphan nobody accounted for. Mark it or wire it.

    Fires in both directions: a table nobody has built yet, and a table whose
    producer was deleted out from under it. The second is how #314 found the four
    PM mirrors — the sidecar's deletion orphaned them in one commit, and this is
    what refused to let that land silently.
    """
    unmarked = sorted(
        path
        for path, cls in classes.items()
        if cls.__name__ not in produced and _marker(cls) is None
    )
    assert not unmarked, (
        "these mapped classes have no producer outside their model module and no "
        f"IMPLEMENTATION_STATUS marker: {unmarked}. Either wire them to a source, or "
        f"mark the module/class with one of {sorted(MARKER_STATUSES)} and a tracking "
        "issue (see #182 for `declared`, #314 for `retired`)."
    )


def test_no_stale_markers(classes, produced):
    """A table someone finally wired must lose its marker — the tier stays honest."""
    stale = sorted(
        path
        for path, cls in classes.items()
        if cls.__name__ in produced and (_marker(cls) or {}).get("status") in MARKER_STATUSES
    )
    assert not stale, (
        f"these classes carry a marker but a live module now references them: {stale}. "
        "Drop the marker (and its coverage omit entry) — the table is implemented. "
        "A `retired` table that gained a reader is a resurrection, and the marker "
        "must not outlive it."
    )


def test_every_marker_names_an_open_tracking_issue(classes):
    """A marker without a live issue is a TODO nobody owns — for either status."""
    problems = []
    for path, cls in classes.items():
        marker = _marker(cls)
        if marker is None:
            continue
        if marker["status"] not in MARKER_STATUSES:
            problems.append(
                f"{path}: unknown status {marker['status']!r} "
                f"(expected one of {sorted(MARKER_STATUSES)})"
            )
            continue
        issues = marker["issues"]
        if not isinstance(issues, tuple) or not issues:
            problems.append(f"{path}: tracking issues must be a non-empty tuple, got {issues!r}")
            continue
        unknown = [i for i in issues if i not in OPEN_TRACKING_ISSUES]
        if unknown:
            problems.append(f"{path}: {unknown} not in OPEN_TRACKING_ISSUES")
        if not isinstance(marker["rationale"], str) or not marker["rationale"].strip():
            problems.append(f"{path}: needs a one-line rationale")
    assert not problems, problems


def test_marked_modules_are_excluded_from_the_coverage_gate(classes):
    """A wholly marked module must not pad the 80% gate with code nothing runs.

    Same rule for both statuses: `declared` code has never run and `retired` code
    has stopped running, and either way the only lines coverage sees are the class
    body executing on import."""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    omitted = set(config["tool"]["coverage"]["run"]["omit"])

    by_module: dict[str, list[type[DeclarativeBase]]] = {}
    for cls in classes.values():
        by_module.setdefault(cls.__module__, []).append(cls)

    missing = []
    for module, members in sorted(by_module.items()):
        if not all((_marker(cls) or {}).get("scope") == "module" for cls in members):
            continue
        path = next(p for p in _source_files() if _module_name(p) == module)
        rel = str(path.relative_to(REPO_ROOT))
        if rel not in omitted:
            missing.append(rel)
    assert not missing, f"declared modules missing from [tool.coverage.run] omit: {missing}"
