"""Shared fixtures for the pipeline suite."""

import os

import pytest

import usa_wa_pipeline

_HERMETIC_ENV = (
    "USA_WA_PIPELINE_DB",
    "USA_WA_PIPELINE_HERMETIC",
    "USA_WA_RAW_ROOT",
    "DATABASE_URL",
)


@pytest.fixture(scope="session")
def hermetic_build(tmp_path_factory):
    """A full `dbt build` into a throwaway duckdb, with no database anywhere.

    Session-scoped: the build is the expensive part (~6s), and the build-green,
    contract and type-fidelity checks all read it. The env is set for the
    build alone and restored before the first test sees the path, so the
    hermetic marker never leaks into an unrelated test.
    """
    from dbt.cli.main import dbtRunner

    tmp_path = tmp_path_factory.mktemp("hermetic")
    previous = {key: os.environ.get(key) for key in _HERMETIC_ENV}
    os.environ["USA_WA_PIPELINE_DB"] = str(tmp_path / "test.duckdb")
    os.environ["USA_WA_PIPELINE_HERMETIC"] = "1"
    os.environ["USA_WA_RAW_ROOT"] = str(tmp_path / "raw")
    os.environ.pop("DATABASE_URL", None)
    try:
        result = dbtRunner().invoke(
            [
                "build",
                "--project-dir",
                str(usa_wa_pipeline.PROJECT_DIR),
                "--profiles-dir",
                str(usa_wa_pipeline.PROJECT_DIR),
                "--target-path",
                str(tmp_path / "target"),
                "--log-path",
                str(tmp_path / "logs"),
            ]
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert result.success, f"dbt build failed: {result.exception}"
    return tmp_path / "test.duckdb"
