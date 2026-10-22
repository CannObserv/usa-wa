"""Every third-party import in a workspace package is declared by that package (#527).

Six packages imported ``sqlalchemy`` while only ``clearinghouse-core`` and
``clearinghouse-domain-legislative`` declared it. Everyone else got it, and its
floor, transitively. That held until #504 had ``usa-wa-api`` use
``postgresql.distinct_on``, which exists only in 2.1: its correctness then
rested on a floor it did not state. ``import-linter`` cannot see this — it runs
with ``include_external_packages = false`` and checks layering, not
declarations.

* **Scope: third-party imports in ``packages/<p>/src``.** Standard-library and
  workspace-sibling imports are out; so are relative imports. Function-local
  imports count — an import that runs is a dependency wherever it sits.
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
    return {_normalize(NAME.match(r).group(1)) for r in _project(pyproject).get("dependencies", [])}


def _top_level_imports(src: Path) -> set[str]:
    """Every absolute import's top-level name under ``src``, function-local ones included."""
    names: set[str] = set()
    for path in src.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def _workspace_modules(pyprojects: list[Path]) -> set[str]:
    """The import names the workspace members themselves provide."""
    return {
        d.name
        for p in pyprojects
        for d in (p.parent / "src").iterdir()
        if (d / "__init__.py").is_file()
    }


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


def test_the_glob_found_the_workspace():
    assert len(PYPROJECTS) > 1, "found no workspace members - the glob broke"


def test_every_third_party_import_is_declared():
    bad = sorted(f"{package}: {top}" for package, tops in _findings().items() for top in tops)
    assert not bad, (
        "these packages import a third-party module they do not declare; add it to the "
        "package's [project].dependencies with the floor it needs and a <next-major cap:\n  "
        + "\n  ".join(bad)
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
