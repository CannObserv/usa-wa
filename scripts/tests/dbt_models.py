"""Where a dbt project's Python models live (#535).

Not a test module. ``test_dbt_model_imports`` (the import contracts) and
``test_declared_dependencies`` (declare what you import) both scan the pipeline's
models, and import from here so the two can never disagree about which files those
are. ``conftest.py`` in this directory puts it on ``sys.path`` regardless of mode.

dbt loads Python models from ``model-paths`` alone, so that is the scan root — read
from ``dbt_project.yml`` rather than globbed from the project dir, because an ad-hoc
in-tree ``dbt run`` writes compiled ``.py`` copies of every model under ``target/``.
"""

from pathlib import Path

import yaml

#: dbt's own default when ``dbt_project.yml`` names no ``model-paths``.
DEFAULT_MODEL_PATHS = ("models",)


def model_paths(dbt_project: Path) -> list[str]:
    """The project's ``model-paths`` (relative to ``dbt_project``), as its config names them."""
    config = yaml.safe_load((dbt_project / "dbt_project.yml").read_text()) or {}
    paths = config.get("model-paths")
    return list(DEFAULT_MODEL_PATHS if paths is None else paths)


def model_files(dbt_project: Path) -> list[Path]:
    """Every Python model under the project's ``model-paths``, sorted."""
    return sorted(f for p in model_paths(dbt_project) for f in (dbt_project / p).rglob("*.py"))
