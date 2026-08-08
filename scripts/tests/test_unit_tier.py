"""The unit tier must stay runnable with no database at all (#185).

Before this guard, the workspace-root ``conftest.py`` raised at *module import* when
``TEST_DATABASE_URL`` was unset, so the entire suite — including pure-function tests
of normalizers, projectors and span arithmetic — was un-runnable off a provisioned
box. It also imported ``usa_wa_adapter_legislature.transport`` (Layer 3) at module
scope, so a Layer-1 ``clearinghouse-core`` test transitively pulled in the WSL SOAP
stack.

``pytest -m 'not db'`` is now a real tier. These tests pin the three properties that
make it one, because each is easy to undo by accident:

1. the base conftest imports no jurisdiction package,
2. it imports cleanly with no ``TEST_DATABASE_URL`` — and the CR #191 production-DSN
   guard still fires in that state,
3. every DB-touching test carries the ``db`` marker, whether it gets there through the
   shared fixtures (automatically) or by opening its own engine (explicitly).

Pure file parse + one module import — no DB; runs everywhere.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).parent.parent.parent  # scripts/tests/ → repo
PYPROJECT = REPO / "pyproject.toml"
ROOT_CONFTEST = REPO / "conftest.py"

# Layer 3/4 — the per-jurisdiction adapters, API and sidecar. The shared harness sits
# below them; anything it imports is imported by every package's tests.
JURISDICTION_PACKAGE_PREFIXES = ("usa_wa_",)


def _test_files() -> list[Path]:
    """Every test module in the workspace."""
    return sorted(REPO.glob("packages/*/tests/**/test_*.py")) + sorted(
        REPO.glob("scripts/tests/test_*.py")
    )


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported at any level of ``path``."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _load_root_conftest(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the root conftest fresh with every database variable stripped.

    ``monkeypatch.delenv`` records the prior values, so the conftest's own rewrite of
    ``DATABASE_URL`` during this import is undone on teardown and the live session
    keeps the sentinel it started with.
    """
    for name in ("TEST_DATABASE_URL", "DATABASE_URL", "DATABASE_URL_OWNER"):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location("_root_conftest_probe", ROOT_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_root_conftest(config: pytest.Config) -> ModuleType:
    """The root conftest object pytest actually registered for this run.

    Fetched from the plugin manager, not ``import conftest``: every conftest in the
    workspace is imported under the module name ``conftest``, so the last one wins in
    ``sys.modules`` and a plain import silently returns some package's file instead.
    pytest registers each one as a plugin keyed by its path, which is unambiguous.
    """
    module = config.pluginmanager.get_plugin(str(ROOT_CONFTEST))
    assert module is not None, f"{ROOT_CONFTEST} is not registered — is it still the root conftest?"
    return module


class _FakeItem:
    """The two attributes ``pytest_collection_modifyitems`` touches."""

    def __init__(self, *fixturenames: str) -> None:
        self.fixturenames = list(fixturenames)
        self.markers: list[Any] = []

    def add_marker(self, marker: Any) -> None:
        self.markers.append(marker)

    @property
    def marker_names(self) -> set[str]:
        return {m.name for m in self.markers}


def test_the_db_marker_is_registered() -> None:
    """An unregistered marker is a typo away from silently selecting nothing."""
    markers = tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("db:") for m in markers), (
        "no 'db' marker registered in [tool.pytest.ini_options]; "
        "`pytest -m 'not db'` would deselect nothing"
    )


def test_the_base_conftest_imports_no_jurisdiction_package() -> None:
    """Layer-1 tests must not drag in the Layer-3 adapter stack.

    The root conftest is loaded for every run, including ``pytest packages/
    clearinghouse-core``. An import here is an import everywhere.
    """
    offenders = {
        root
        for root in _imported_roots(ROOT_CONFTEST)
        if root.startswith(JURISDICTION_PACKAGE_PREFIXES)
    }
    assert offenders == set(), (
        f"root conftest.py imports jurisdiction package(s) {sorted(offenders)}; "
        "move the fixture that needs them into that package's tests/conftest.py"
    )


def test_the_base_conftest_imports_without_a_test_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DB-free tier's entry condition: no database env, no import error."""
    module = _load_root_conftest(monkeypatch)

    assert module.TEST_DATABASE_URL is None, "the probe did not run with the DSN stripped"


def test_the_production_guard_applies_without_a_test_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CR #191 finding 1 must not be conditional on the test DSN being configured.

    A unit-tier run is exactly the run most likely to happen in a shell with the
    production env loaded and no test database — the guard has to fire there too.
    """
    module = _load_root_conftest(monkeypatch)

    assert os.environ["DATABASE_URL"] == module.BLOCKED_DATABASE_URL
    assert os.environ["DATABASE_URL_OWNER"] == module.BLOCKED_DATABASE_URL


def test_items_requesting_the_shared_db_fixtures_are_marked_db(pytestconfig: pytest.Config) -> None:
    """The marker sweep is a hook, not 102 hand-edited files, so it cannot drift."""
    conftest = _live_root_conftest(pytestconfig)
    engine_item = _FakeItem("test_engine", "anyio_backend")
    session_item = _FakeItem("db_session")
    transitive_item = _FakeItem("client", "db_session", "test_engine")
    pure_item = _FakeItem("monkeypatch", "tmp_path")

    conftest.pytest_collection_modifyitems([engine_item, session_item, transitive_item, pure_item])

    assert "db" in engine_item.marker_names
    assert "db" in session_item.marker_names
    assert "db" in transitive_item.marker_names
    assert pure_item.marker_names == set()


def test_tests_that_open_their_own_engine_declare_the_db_marker() -> None:
    """Integration tests bypassing ``db_session`` are invisible to the fixture sweep.

    They build an engine against ``TEST_DATABASE_URL`` directly, so nothing in their
    fixture closure gives them away — the marker has to be written down.
    """
    unmarked = [
        path.relative_to(REPO).as_posix()
        for path in _test_files()
        if path != Path(__file__)
        and "create_async_engine(" in (source := path.read_text())
        and "mark.db" not in source
    ]
    assert unmarked == [], (
        f"{unmarked} open their own engine but carry no `db` marker; "
        "`pytest -m 'not db'` would try to run them without a database"
    )
