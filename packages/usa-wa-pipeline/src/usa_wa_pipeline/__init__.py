"""usa-wa-pipeline: the dataset-publication dbt project and its Python surface.

`PROJECT_DIR` locates the in-repo dbt project. It resolves relative to this
file, which holds for the editable workspace install this single-VM deployment
runs everywhere; a built wheel would not carry the dbt project, and nothing
ships one.
"""

from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2] / "dbt"
