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

The cost is a dependency on ``importlinter.application`` internals (``use_cases``,
``app_config.settings``, ``ports.building``). A version that moves them fails here
loudly; ``test_every_model_is_grafted`` guards the quiet way — a graft that adds nothing
would keep every contract vacuously.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
from dbt_models import model_files
from grimp import ImportGraph
from importlinter.application import use_cases
from importlinter.application.app_config import settings
from importlinter.application.ports.building import GraphBuilder
from importlinter.configuration import configure

REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "pyproject.toml"
DBT_PROJECT = REPO / "packages" / "usa-wa-pipeline" / "dbt"
#: The package the models are grafted under — the one whose contracts bind them.
PACKAGE = "usa_wa_pipeline"


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


def _resolve(name: str, graph: ImportGraph) -> str | None:
    """The deepest module in the graph that ``name`` names or lies inside, as grimp resolves
    ``from a.b import c`` to ``a.b.c`` only when that is a module. ``None`` for a name outside
    the root packages — stdlib and third-party, which the contracts exclude anyway."""
    parts = name.split(".")
    while parts and ".".join(parts) not in graph.modules:
        parts.pop()
    return ".".join(parts) or None


def graft(graph: ImportGraph, dbt_project: Path) -> list[str]:
    """Add every model under ``dbt_project`` to ``graph`` with its imports; return their names."""
    grafted = []
    for path in model_files(dbt_project):
        module = model_module(dbt_project, path)
        assert module not in graph.modules, f"{module} already names a real module"
        graph.add_module(module)
        source = path.read_text()
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if not isinstance(node, ast.Import | ast.ImportFrom):
                continue
            for name in _imported_names(node, path):
                if imported := _resolve(name, graph):
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
        graph = self._inner.build(
            root_package_names=root_package_names,
            cache_dir=cache_dir,
            include_external_packages=include_external_packages,
            exclude_type_checking_imports=exclude_type_checking_imports,
        )
        self.grafted = graft(graph, self._dbt_project)
        self.graph = graph
        return graph


def lint(dbt_project: Path) -> tuple[bool, GraftingGraphBuilder]:
    """Run every contract in the root ``pyproject.toml`` with ``dbt_project``'s models grafted
    in. The report goes to stdout, as ``lint-imports`` prints it."""
    configure()
    real = settings.GRAPH_BUILDER
    builder = GraftingGraphBuilder(real, dbt_project)
    settings.configure(GRAPH_BUILDER=builder)
    try:
        passed = use_cases.lint_imports(
            config_filename=str(PYPROJECT), cache_dir=None, no_logo=True
        )
    finally:
        settings.configure(GRAPH_BUILDER=real)
    return passed, builder


def test_the_model_scan_found_the_models():
    assert len(model_files(DBT_PROJECT)) > 1, "found no dbt Python models - the scan broke"


def test_every_model_is_grafted():
    """Each model is in the graph with at least one edge — every model imports
    ``usa_wa_pipeline.frames``, so a model with none means the graft lost its imports."""
    _, builder = lint(DBT_PROJECT)
    assert builder.graph is not None, "import-linter never asked the builder for a graph"
    assert builder.grafted == [model_module(DBT_PROJECT, p) for p in model_files(DBT_PROJECT)]
    graph = builder.graph
    edgeless = [m for m in builder.grafted if not graph.find_modules_directly_imported_by(m)]
    assert not edgeless, f"grafted with no imports: {edgeless}"


def test_the_dbt_models_keep_every_import_contract(capsys):
    passed, _ = lint(DBT_PROJECT)
    assert passed, (
        "a dbt Python model breaks an import contract; the report:\n" + capsys.readouterr().out
    )


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
def test_a_real_violation_in_a_model_is_rejected(tmp_path, capsys, probe, contract):
    """The real models plus one illegal one, linted as the gate lints them.

    A copy, unlike ``test_import_contracts``'s probes, because the models are parsed rather
    than resolved through the editable install — the copy is analysed exactly as the
    original is. It also keeps a probe out of the real ``models/``, where a concurrent
    ``dbt run`` would load it.
    """
    shutil.copy(DBT_PROJECT / "dbt_project.yml", tmp_path)
    shutil.copytree(DBT_PROJECT / "models", tmp_path / "models")
    (tmp_path / "models" / "staging" / "_contract_probe.py").write_text(probe)

    passed, builder = lint(tmp_path)

    output = " ".join(capsys.readouterr().out.split())
    assert not passed, f"the linter accepted an illegal model import:\n{output}"
    assert f"{contract} BROKEN" in output, output
    assert "usa_wa_pipeline.dbt.models.staging._contract_probe" in output, output
    assert len(builder.grafted) == len(model_files(DBT_PROJECT)) + 1
