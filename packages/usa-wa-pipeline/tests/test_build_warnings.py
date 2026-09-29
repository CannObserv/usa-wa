"""The nightly's dbt-warning gate (#412 PR E).

A dbt ``warn`` never changes ``dbt build``'s exit code, so before this gate a warning
reached only the journal. ``succession-invariants`` used to email on a low chamber count;
once PR E disables it, the ``assignments_chamber_vacancy`` warning (and the seat-occupancy
ratchet's "ratchet me down") must reach the operator some other way. This stage reads the
build's ``run_results.json`` and fails the run, counted, on any node that did not pass.
"""

from __future__ import annotations

import json

import pytest

from clearinghouse_core.job import EXIT_DEGRADED, OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK
from clearinghouse_core.testing import patch_job_runtime
from usa_wa_pipeline.build_warnings import JOB_SLUG, main, summarize


def _results(*statuses: tuple[str, str]) -> dict:
    return {
        "args": {"which": "build"},
        "metadata": {"generated_at": "2026-09-29T08:02:00Z"},
        "results": [
            {"unique_id": uid, "status": status, "failures": 1 if status == "warn" else 0}
            for uid, status in statuses
        ],
    }


CLEAN = _results(
    ("model.usa_wa_pipeline.assignments", "success"),
    ("test.usa_wa_pipeline.assignments_chamber_vacancy", "pass"),
)


def test_slug_is_stable() -> None:
    assert JOB_SLUG == "dbt-build-warnings"


def test_a_clean_build_is_ok() -> None:
    result = summarize(CLEAN)
    assert result.outcome == OUTCOME_OK
    assert result.counters["nodes"] == 2
    assert result.counters["warned"] == 0


def test_a_warning_fails_the_stage_and_names_the_node() -> None:
    result = summarize(
        _results(
            ("model.usa_wa_pipeline.assignments", "success"),
            ("test.usa_wa_pipeline.assignments_chamber_vacancy", "warn"),
        )
    )
    assert result.outcome == OUTCOME_FAILED
    assert result.resolved_exit_code() == 1
    assert result.counters["warned"] == 1
    assert result.counters["not_clean"] == ["test.usa_wa_pipeline.assignments_chamber_vacancy"]


@pytest.mark.parametrize("status", ["error", "fail", "skipped", "runtime error"])
def test_any_status_but_pass_or_success_fails_too(status) -> None:
    """``dbt build`` exits non-zero on these, so the nightly aborts first; if one ever
    reaches this stage anyway, it must not read as clean."""
    result = summarize(_results(("test.usa_wa_pipeline.x", status)))
    assert result.outcome == OUTCOME_FAILED
    assert result.counters["not_clean"] == ["test.usa_wa_pipeline.x"]


def test_an_empty_run_is_degraded_not_clean() -> None:
    """No nodes means no build was read: an empty file must not pass as zero warnings."""
    result = summarize(_results())
    assert result.outcome == OUTCOME_DEGRADED


def test_a_run_that_was_not_a_build_is_degraded() -> None:
    """A ``dbt test`` or ``dbt run`` left in the target dir covers only half the nodes."""
    results = CLEAN | {"args": {"which": "test"}}
    assert summarize(results).outcome == OUTCOME_DEGRADED


def test_main_reads_the_file_and_exits_by_outcome(tmp_path, monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    path = tmp_path / "run_results.json"
    path.write_text(json.dumps(CLEAN))
    assert main(["--run-results", str(path)]) == 0

    warned = _results(("test.usa_wa_pipeline.assignments_chamber_vacancy", "warn"))
    path.write_text(json.dumps(warned))
    assert main(["--run-results", str(path)]) == 1


def test_a_missing_file_is_degraded(tmp_path, monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    assert main(["--run-results", str(tmp_path / "absent.json")]) == EXIT_DEGRADED
