"""The pipeline's dbt Python models are held to every import contract (#535).

The models live in ``packages/usa-wa-pipeline/dbt/models/``, outside ``src/``, so they
are no part of the ``usa_wa_pipeline`` package and ``import-linter`` — which analyses
only ``root_packages`` — never read them. Every contract that binds the pipeline as an
importer (the layer order, the transport ban, the retired Postgres tier) covered the
helpers the models call and not the models, while the contracts' own comments presented
the models as gated. Same failure class as #533: a rule stated and enforced nowhere.

The fix runs ``import-linter`` itself over the models rather than restating its rules:

* **Parsed, never imported.** Each model's imports are read with ``ast`` — function-local
  ones included, since dbt's ``model(dbt, session)`` is where a model would put one.
* **Grafted into the real graph** as ``usa_wa_pipeline.dbt.<model path>``, through the
  ``GRAPH_BUILDER`` port import-linter's own tests use to supply a graph. Under the
  package name, every contract naming ``usa_wa_pipeline`` covers them.
* **Every contract, read from the root ``pyproject.toml``.** Nothing is copied: the
  ``forbidden_modules``, the wildcards, the indirect-chain rule and the layer order are
  import-linter's, applied exactly as ``lint-imports`` applies them to ``src/``.

**Run at commit too (#551)**, by the ``dbt-model-imports`` pre-commit hook: the
``import-linter`` hook beside ruff cannot see the models, and the repo has no PR CI, so
until #551 a model edit committed without pre-ship waited for the next suite run.

The cost is a dependency on ``importlinter.application`` internals (``use_cases``,
``app_config.settings``, ``ports.building``). A version that moves them fails here
loudly; ``test_every_model_is_grafted`` guards the quiet way — a graft that adds nothing
would keep every contract vacuously.
"""

from __future__ import annotations

import ast
import contextlib
import io
import re
import shutil
from collections.abc import Set
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from dbt_models import model_files, model_paths
from importlinter.application import use_cases
from importlinter.application.app_config import settings
from importlinter.application.ports.building import GraphBuilder
from importlinter.configuration import configure
from precommit_hooks import local_hooks

if TYPE_CHECKING:
    # Annotations only: grimp is import-linter's dependency, not the root dev group's, so
    # this test does not import it at runtime and needs no declaration of its own.
    from grimp import ImportGraph

REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "pyproject.toml"
#: The pre-commit hook that runs this gate at commit (#551).
HOOK = "dbt-model-imports"
DBT_PROJECT = REPO / "packages" / "usa-wa-pipeline" / "dbt"
#: The package the models are grafted under — the one whose contracts bind them.
PACKAGE = "usa_wa_pipeline"
#: A terminal escape sequence. Rich styles the report (``FORCE_COLOR``, a TTY), and a styled
#: ``BROKEN`` would no longer follow its contract's name as plain text.
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def model_module(dbt_project: Path, path: Path) -> str:
    """The graph name a model is grafted under (``…/dbt/models/a/b.py`` →
    ``usa_wa_pipeline.dbt.models.a.b``)."""
    return ".".join([PACKAGE, "dbt", *path.relative_to(dbt_project).with_suffix("").parts])


def _imported_names(node: ast.Import | ast.ImportFrom, path: Path) -> list[str]:
    """The most specific dotted name each alias of an import statement could mean."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    assert node.level == 0 and node.module, (
        f"{path}:{node.lineno}: a relative import in a dbt model resolves against nothing"
    )
    return [f"{node.module}.{alias.name}" for alias in node.names]


def _resolve(name: str, modules: Set[str]) -> str | None:
    """The deepest of ``modules`` that ``name`` names or lies inside, as grimp resolves
    ``from a.b import c`` to ``a.b.c`` only when that is a module. ``None`` for a name outside
    the root packages — stdlib and third-party, which the contracts exclude anyway."""
    parts = name.split(".")
    while parts and ".".join(parts) not in modules:
        parts.pop()
    return ".".join(parts) or None


def graft(graph: ImportGraph, dbt_project: Path) -> list[str]:
    """Add every model under ``dbt_project`` to ``graph`` with its imports; return their names."""
    # Resolved against the real modules only, snapshotted once: grimp rebuilds
    # `graph.modules` after every `add_import`, and no model is importable as a module.
    real = frozenset(graph.modules)
    grafted = []
    for path in model_files(dbt_project):
        module = model_module(dbt_project, path)
        assert module not in real, f"{module} already names a real module"
        graph.add_module(module)
        source = path.read_text()
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for name in _imported_names(node, path):
                if imported := _resolve(name, real):
                    graph.add_import(
                        importer=module,
                        imported=imported,
                        line_number=node.lineno,
                        line_contents=lines[node.lineno - 1].strip(),
                    )
        grafted.append(module)
    return grafted


class GraftingGraphBuilder(GraphBuilder):
    """import-linter's own graph, with a dbt project's models grafted in."""

    def __init__(self, inner: GraphBuilder, dbt_project: Path) -> None:
        self._inner = inner
        self._dbt_project = dbt_project
        self.graph: ImportGraph | None = None
        self.grafted: list[str] = []

    def build(
        self,
        root_package_names: list[str],
        cache_dir: str | None,
        include_external_packages: bool = False,
        exclude_type_checking_imports: bool = False,
    ) -> ImportGraph:
        """The inner builder's graph, with every model grafted in (see :func:`graft`)."""
        # The graft keeps every import, `if TYPE_CHECKING:` ones included; a config that
        # excludes them from `src/` would hold the models to a stricter rule than `src/`.
        assert not exclude_type_checking_imports, (
            "exclude_type_checking_imports is set: teach graft() to drop TYPE_CHECKING imports"
        )
        graph = self._inner.build(
            root_package_names=root_package_names,
            cache_dir=cache_dir,
            include_external_packages=include_external_packages,
            exclude_type_checking_imports=exclude_type_checking_imports,
        )
        self.grafted = graft(graph, self._dbt_project)
        self.graph = graph
        return graph


def lint(dbt_project: Path) -> tuple[bool, GraftingGraphBuilder, str]:
    """Run every contract in the root ``pyproject.toml`` with ``dbt_project``'s models grafted
    in. Returns the verdict, the builder, and the report as ``lint-imports`` prints it, with
    any terminal styling stripped.

    Debug mode, so an exception — a graft assertion, or an internals move in import-linter —
    raises with its traceback instead of being rendered and reported as a broken contract."""
    configure()
    real = settings.GRAPH_BUILDER
    builder = GraftingGraphBuilder(real, dbt_project)
    settings.configure(GRAPH_BUILDER=builder)
    report = io.StringIO()
    try:
        with contextlib.redirect_stdout(report):
            passed = use_cases.lint_imports(
                config_filename=str(PYPROJECT), cache_dir=None, is_debug_mode=True, no_logo=True
            )
    finally:
        settings.configure(GRAPH_BUILDER=real)
    return passed, builder, ANSI.sub("", report.getvalue())


@pytest.fixture(scope="module")
def linted() -> tuple[bool, GraftingGraphBuilder, str]:
    """The real models, linted once for every test that reads the result."""
    return lint(DBT_PROJECT)


def test_the_model_scan_found_the_models():
    assert len(model_files(DBT_PROJECT)) > 1, "found no dbt Python models - the scan broke"


def test_every_model_is_grafted(linted):
    """Each model is in the graph with at least one edge — every model imports
    ``usa_wa_pipeline.frames``, so a model with none means the graft lost its imports."""
    _, builder, _ = linted
    assert builder.graph is not None, "import-linter never asked the builder for a graph"
    assert builder.grafted == [model_module(DBT_PROJECT, p) for p in model_files(DBT_PROJECT)]
    graph = builder.graph
    edgeless = [m for m in builder.grafted if not graph.find_modules_directly_imported_by(m)]
    assert not edgeless, f"grafted with no imports: {edgeless}"


def test_the_dbt_models_keep_every_import_contract(linted):
    passed, _, report = linted
    assert passed, "a dbt Python model breaks an import contract; the report:\n" + report


def test_a_model_outside_model_paths_is_not_scanned(tmp_path):
    """``target/`` holds dbt's compiled copies of every model; only ``model-paths`` loads."""
    (tmp_path / "dbt_project.yml").write_text('model-paths: ["src_models"]\n')
    for relative in ("src_models/a.py", "models/b.py", "target/compiled/c.py"):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("")
    assert model_files(tmp_path) == [tmp_path / "src_models" / "a.py"]


@pytest.mark.parametrize(
    ("probe", "contract"),
    [
        # A model driving a wire — the rule the transport contract's comment claimed for the
        # models before anything enforced it. Function-local, where dbt models import.
        (
            "def model(dbt, session):\n"
            "    from usa_wa_adapter_pdc.transport import PDCClient  # noqa: F401\n",
            "Facts and the pipeline never depend on a transport",
        ),
        # A model reaching the retired tier through a module that is not itself forbidden:
        # the operator CLI imports the store, and this contract counts the chain.
        (
            "from usa_wa_adapter_legislature.operators import cli  # noqa: F401\n",
            "The pipeline, the API and the raw harvests never import the retired Postgres tier",
        ),
        # A model importing the deployment above it.
        (
            "from usa_wa_api import api  # noqa: F401\n",
            "Layers: core < domain < common < adapter < facts < pipeline < deployment",
        ),
    ],
)
def test_a_real_violation_in_a_model_is_rejected(tmp_path, probe, contract):
    """The real models plus one illegal one, linted as the gate lints them.

    A copy, unlike ``test_import_contracts``'s probes, because the models are parsed rather
    than resolved through the editable install — the copy is analysed exactly as the
    original is. It also keeps a probe out of the real ``models/``, where a concurrent
    ``dbt run`` would load it.
    """
    shutil.copy(DBT_PROJECT / "dbt_project.yml", tmp_path)
    for path in model_paths(DBT_PROJECT):
        shutil.copytree(DBT_PROJECT / path, tmp_path / path)
    probe_path = tmp_path / model_paths(DBT_PROJECT)[0] / "staging" / "_contract_probe.py"
    probe_path.write_text(probe)

    passed, builder, report = lint(tmp_path)

    # Rich wraps the report to the console width (COLUMNS, or the terminal pytest runs in),
    # dropping the space before a wrapped verdict and folding a long module name mid-word,
    # so compare with every whitespace character removed.
    squashed = "".join(report.split())
    assert not passed, f"the linter accepted an illegal model import:\n{report}"
    assert "".join(f"{contract} BROKEN".split()) in squashed, report
    assert model_module(tmp_path, probe_path) in squashed, report
    assert len(builder.grafted) == len(model_files(DBT_PROJECT)) + 1


def _hook() -> dict:
    hooks = local_hooks()
    assert HOOK in hooks, f"{HOOK} is not wired into .pre-commit-config.yaml"
    return hooks[HOOK]


def test_the_model_gate_runs_at_commit():
    """The model gate and ``test_declared_dependencies`` (whose scan takes in the models
    since #535) run at commit (#551), on every file whose edit can change their verdict on
    a model.

    ``import-linter`` runs on every commit but cannot see a model, and ``dbt-build`` runs
    on every pipeline commit but checks no import. Without this hook a model importing a
    transport, or a package it does not declare, is caught only at the next suite run.
    """
    hook = _hook()
    for test in ("test_dbt_model_imports.py", "test_declared_dependencies.py"):
        assert f"scripts/tests/{test}" in hook["entry"], hook["entry"]
    assert "--no-cov" in hook["entry"], "neither coverage gate measures a slice"
    # Filenames appended to a pytest command are collected as test files.
    assert hook["pass_filenames"] is False
    files = re.compile(hook["files"])
    triggers = [
        *(path.relative_to(REPO).as_posix() for path in model_files(DBT_PROJECT)),
        "pyproject.toml",  # the contracts
        "packages/usa-wa-pipeline/pyproject.toml",  # the declarations
        "packages/usa-wa-pipeline/dbt/dbt_project.yml",  # `model-paths`
        "scripts/tests/dbt_models.py",  # the scan both tests share
        "scripts/tests/workspace_modules.py",  # the module-name rule the declarations scan uses
        "scripts/tests/precommit_hooks.py",  # the hook loader the gate imports
        "scripts/tests/test_dbt_model_imports.py",  # the gate itself
        "scripts/tests/test_declared_dependencies.py",
        "scripts/tests/conftest.py",  # puts the shared helpers on the path both tests import
    ]
    assert [t for t in triggers if not files.search(t)] == []
    # dbt's compiled copies are not models; `src/` is `import-linter`'s, every commit.
    for other in (
        "packages/usa-wa-pipeline/dbt/target/compiled/stg_x.py",
        "packages/usa-wa-pipeline/src/usa_wa_pipeline/frames.py",
    ):
        assert not files.search(other), other
