"""The repo's own pre-commit hooks, as ``.pre-commit-config.yaml`` defines them (#553).

Not a test module. Every guard that pins a hook's wiring (``test_hook_registration_gate``,
``test_pipeline_gate``, ``test_import_contracts``, ``test_dbt_model_imports``) reads the
hooks from here, so a change to the config's shape is made once. ``conftest.py`` in this
directory puts it on ``sys.path`` regardless of mode.

``local`` repos only: a hook from a remote repo is listed in the config by ``id`` (and
``args``) alone, its ``entry`` living in that repo's manifest, so a remote definition has
nothing a wiring guard here could pin. #553 measured that no remote repo defines an id a
guard looks up; the two callers that read every repo got the same hooks either way.
"""

from pathlib import Path

import yaml

PRECOMMIT = Path(__file__).resolve().parents[2] / ".pre-commit-config.yaml"


def local_hook_entries() -> list[dict]:
    """Every hook a ``local`` repo defines, in config order, duplicate ids included."""
    config = yaml.safe_load(PRECOMMIT.read_text())
    return [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]


def local_hooks() -> dict[str, dict]:
    """The ``local`` hooks by id; of two hooks sharing an id, the later one."""
    return {hook["id"]: hook for hook in local_hook_entries()}
