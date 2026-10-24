"""Every third-party and workspace-sibling import in a package is declared by it (#527, #533).

Six packages imported ``sqlalchemy`` while only ``clearinghouse-core`` and
``clearinghouse-domain-legislative`` declared it. Everyone else got it, and its
floor, transitively. That held until #504 had ``usa-wa-api`` use
``postgresql.distinct_on``, which exists only in 2.1: its correctness then
rested on a floor it did not state. ``import-linter`` cannot see this — it runs
with ``include_external_packages = false`` and checks layering, not
declarations.

* **Scope: third-party imports in ``packages/<p>/src``.** Standard-library
  and relative imports are out; workspace siblings have their own bullet
  below. Function-local imports count — an import that runs is a
  dependency wherever it sits.
* **Workspace siblings, the same rule (#533).** ``usa-wa-pipeline`` imported
  ``usa-wa-facts-seats``, ``usa-wa-common`` and the domain without declaring
  any of them; facts arrived only because the root ``dev`` group installs every
  member. A sibling import is declared too, unless the layers contract's own
  ``ignore_imports`` names it — that list is where an import that is *not* a
  dependency (``clearinghouse_core.testing`` registering the domain's tables)
  is already justified, so this rule reuses it rather than keeping a second.
  The reverse — declared but never imported — is not checked here.
* **Resolved through the venv**, by ``importlib.metadata.packages_distributions``.
  An import no installed distribution provides counts as undeclared, so a
  typo or a missing install fails here rather than passing silently.
* **A namespace package passes when any of its distributions is declared**
  (``google`` → ``google-cloud-storage``). Coarse on purpose; no package here
  shares a namespace with a second, undeclared one.
* **No exemption list.** The rule found six more undeclared imports beside
  ``sqlalchemy`` (``python-ulid`` ×3, ``pydantic``, ``httpx``, ``duckdb``);
  #527 declared them all. A new finding gets declared, not excused.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections.abc import Mapping
from functools import cache
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PYPROJECTS = sorted(REPO.glob("packages/*/pyproject.toml"))
NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalize(name: str) -> str:
    """PEP 503 normalization, so ``python_ulid`` and ``Python-ULID`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _project(pyproject: Path) -> dict:
    return tomllib.loads(pyproject.read_text())["project"]


def _declared(pyproject: Path) -> set[str]:
    """The normalized distribution names ``[project].dependencies`` declares."""
    names = set()
    for requirement in _project(pyproject).get("dependencies", []):
        match = NAME.match(requirement)
        assert match, f"{pyproject}: no distribution name in requirement {requirement!r}"
        names.add(_normalize(match.group(1)))
    return names


def _module_name(src: Path, path: Path) -> str:
    """The dotted module a file under ``src`` defines (``pkg/__init__.py`` → ``pkg``)."""
    parts = path.relative_to(src).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(src: Path) -> set[tuple[str, str]]:
    """``(importing module, imported top-level name)`` for every absolute import under ``src``,
    function-local ones included."""
    pairs: set[tuple[str, str]] = set()
    for path in src.rglob("*.py"):
        module = _module_name(src, path)
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                pairs.update((module, alias.name.split(".")[0]) for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                pairs.add((module, node.module.split(".")[0]))
    return pairs


def _top_level_imports(src: Path) -> set[str]:
    """Every absolute import's top-level name under ``src``, function-local ones included."""
    return {top for _, top in _imports(src)}


def _workspace_distributions(pyprojects: list[Path]) -> dict[str, str]:
    """Each workspace import name → the normalized distribution that provides it."""
    return {
        d.name: _normalize(_project(p)["name"])
        for p in pyprojects
        for d in (p.parent / "src").iterdir()
        if (d / "__init__.py").is_file()
    }


def _workspace_modules(pyprojects: list[Path]) -> set[str]:
    """The import names the workspace members themselves provide."""
    return set(_workspace_distributions(pyprojects))


def undeclared(
    pyproject: Path, distributions: Mapping[str, list[str]], workspace: set[str]
) -> set[str]:
    """The third-party top-level imports in a package's ``src`` it does not declare."""
    declared = _declared(pyproject)
    return {
        top
        for top in _top_level_imports(pyproject.parent / "src")
        if top not in sys.stdlib_module_names
        and top not in workspace
        and not {_normalize(d) for d in distributions.get(top, [])} & declared
    }


@cache
def _findings() -> dict[str, set[str]]:
    distributions = packages_distributions()
    workspace = _workspace_modules(PYPROJECTS)
    return {_project(p)["name"]: undeclared(p, distributions, workspace) for p in PYPROJECTS}


def _ignored_imports(root_pyproject: Path) -> set[tuple[str, str]]:
    """The layers contract's ``ignore_imports``, as ``(importer, imported top-level name)``."""
    contracts = tomllib.loads(root_pyproject.read_text())["tool"]["importlinter"]["contracts"]
    return {
        (importer.strip(), imported.strip().split(".")[0])
        for c in contracts
        if c["type"] == "layers"
        for importer, imported in (i.split("->") for i in c.get("ignore_imports", []))
    }


def undeclared_siblings(
    pyproject: Path, workspace: Mapping[str, str], ignored: set[tuple[str, str]]
) -> set[str]:
    """The workspace members a package's ``src`` imports without declaring them."""
    declared = _declared(pyproject) | {_normalize(_project(pyproject)["name"])}
    return {
        workspace[top]
        for module, top in _imports(pyproject.parent / "src")
        if top in workspace and workspace[top] not in declared and (module, top) not in ignored
    }


def test_the_glob_found_the_workspace():
    assert len(PYPROJECTS) > 1, "found no workspace members - the glob broke"


def test_every_third_party_import_is_declared():
    bad = sorted(f"{package}: {top}" for package, tops in _findings().items() for top in tops)
    assert not bad, (
        "these packages import a third-party module they do not declare; add it to the "
        "package's [project].dependencies with the floor it needs and a <next-major cap:\n  "
        + "\n  ".join(bad)
    )


def test_every_workspace_sibling_import_is_declared():
    workspace = _workspace_distributions(PYPROJECTS)
    ignored = _ignored_imports(REPO / "pyproject.toml")
    bad = sorted(
        f"{_project(p)['name']}: {dist}"
        for p in PYPROJECTS
        for dist in undeclared_siblings(p, workspace, ignored)
    )
    assert not bad, (
        "these packages import a workspace member they do not declare; add it to "
        "[project].dependencies and [tool.uv.sources] (workspace = true):\n  " + "\n  ".join(bad)
    )


def _fake_package(tmp_path: Path, declares: list[str], modules: dict[str, str]) -> Path:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f'[project]\nname = "fake"\ndependencies = {declares!r}\n')
    pkg = tmp_path / "src" / "fake"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for name, body in modules.items():
        (pkg / name).write_text(body)
    return pyproject


def test_the_check_sees_an_undeclared_import(tmp_path):
    pyproject = _fake_package(
        tmp_path,
        declares=["httpx>=0.27,<1", "Python_ULID>=3,<4"],
        modules={
            "a.py": "import os\nimport httpx\nfrom sqlalchemy import orm\nfrom . import b\n",
            "b.py": "from ulid import ULID\nfrom fake import a\nfrom usa_wa_common import seats\n",
            "c.py": "def f():\n    import yaml\n",
        },
    )
    distributions = {
        "httpx": ["httpx"],
        "sqlalchemy": ["SQLAlchemy"],
        "ulid": ["python-ulid"],
        "yaml": ["PyYAML"],
    }
    workspace = {"fake", "usa_wa_common"}
    assert undeclared(pyproject, distributions, workspace) == {"sqlalchemy", "yaml"}


def test_a_namespace_import_passes_when_any_of_its_distributions_is_declared(tmp_path):
    pyproject = _fake_package(
        tmp_path,
        declares=["google-cloud-storage>=2,<4"],
        modules={"a.py": "from google.cloud import storage\n"},
    )
    distributions = {"google": ["google-api-core", "google-cloud-storage"]}
    assert undeclared(pyproject, distributions, {"fake"}) == set()


def test_an_import_no_distribution_provides_counts_as_undeclared(tmp_path):
    pyproject = _fake_package(
        tmp_path, declares=[], modules={"a.py": "import not_installed_anywhere\n"}
    )
    assert undeclared(pyproject, {}, {"fake"}) == {"not_installed_anywhere"}


def test_a_requirement_without_a_name_fails_naming_its_file(tmp_path):
    pyproject = _fake_package(tmp_path, declares=["  "], modules={})
    with pytest.raises(AssertionError, match="pyproject.toml"):
        undeclared(pyproject, {}, {"fake"})


def test_the_sibling_check_sees_an_undeclared_workspace_import(tmp_path):
    pyproject = _fake_package(
        tmp_path,
        declares=["usa-wa-common"],
        modules={
            "a.py": "from usa_wa_common import seats\nfrom fake import b\n",
            "b.py": "def f():\n    import usa_wa_facts_seats\n",
            "c.py": "import clearinghouse_domain_legislative\nimport httpx\n",
        },
    )
    workspace = {
        "fake": "fake",
        "usa_wa_common": "usa-wa-common",
        "usa_wa_facts_seats": "usa-wa-facts-seats",
        "clearinghouse_domain_legislative": "clearinghouse-domain-legislative",
    }
    assert undeclared_siblings(pyproject, workspace, set()) == {
        "usa-wa-facts-seats",
        "clearinghouse-domain-legislative",
    }


def test_an_import_the_layers_contract_ignores_is_not_a_sibling_dependency(tmp_path):
    pyproject = _fake_package(
        tmp_path, declares=[], modules={"testing.py": "import clearinghouse_domain_legislative\n"}
    )
    workspace = {"clearinghouse_domain_legislative": "clearinghouse-domain-legislative"}
    ignored = {("fake.testing", "clearinghouse_domain_legislative")}
    assert undeclared_siblings(pyproject, workspace, ignored) == set()
    # Named by module, not by package: a second importer in the same package still counts.
    (tmp_path / "src" / "fake" / "other.py").write_text("import clearinghouse_domain_legislative\n")
    assert undeclared_siblings(pyproject, workspace, ignored) == {
        "clearinghouse-domain-legislative"
    }
