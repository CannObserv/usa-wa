"""The dbt project scaffold is buildable end-to-end (#303).

Drives dbt in-process (``dbtRunner``) against a throwaway duckdb file, so the
suite proves what the pre-commit gate proves: ``dbt build`` parses the project,
loads the seeds, builds every model, and runs every schema/data test. The
scaffold ships a smoke seed + staging model so a green build exercises the whole
harness, not an empty project.
"""

import pytest

import usa_wa_pipeline


def test_project_dir_exists() -> None:
    assert (usa_wa_pipeline.PROJECT_DIR / "dbt_project.yml").is_file()
    assert (usa_wa_pipeline.PROJECT_DIR / "profiles.yml").is_file()


def test_layer_dirs_exist() -> None:
    """The three pipeline layers are laid out per the replatform spec."""
    models = usa_wa_pipeline.PROJECT_DIR / "models"
    for layer in ("staging", "matching", "conformed"):
        assert (models / layer).is_dir(), f"missing models/{layer}/"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_dbt_build_green(tmp_path, monkeypatch) -> None:
    """``dbt build`` on the scaffold exits clean against a throwaway duckdb."""
    from dbt.cli.main import dbtRunner

    monkeypatch.setenv("USA_WA_PIPELINE_DB", str(tmp_path / "test.duckdb"))
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
    assert result.success, f"dbt build failed: {result.exception}"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_dbt_data_test_failure_is_red(tmp_path, monkeypatch) -> None:
    """A failing data test fails the build — the property the commit gate relies on.

    Injects a model that violates its own ``accepted_values`` test into a copy of
    the scaffold; ``dbt build`` on the copy must report failure.
    """
    import shutil

    from dbt.cli.main import dbtRunner

    project = tmp_path / "project"
    shutil.copytree(usa_wa_pipeline.PROJECT_DIR, project)
    bad = project / "models" / "staging" / "stg_bad.sql"
    bad.write_text("select 'not-ok' as label\n")
    (project / "models" / "staging" / "stg_bad.yml").write_text(
        "version: 2\n"
        "models:\n"
        "  - name: stg_bad\n"
        "    columns:\n"
        "      - name: label\n"
        "        data_tests:\n"
        "          - accepted_values:\n"
        "              values: ['ok']\n"
    )
    monkeypatch.setenv("USA_WA_PIPELINE_DB", str(tmp_path / "test.duckdb"))
    result = dbtRunner().invoke(
        [
            "build",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project),
            "--target-path",
            str(tmp_path / "target"),
            "--log-path",
            str(tmp_path / "logs"),
        ]
    )
    assert not result.success or any(
        getattr(r, "status", None) == "fail" for r in getattr(result.result, "results", [])
    ), "a violated data test did not fail dbt build"
