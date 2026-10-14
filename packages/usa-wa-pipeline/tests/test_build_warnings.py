"""The nightly's dbt-warning gate (#412 PR E).

A dbt ``warn`` never changes ``dbt build``'s exit code, so before this gate a warning
reached only the journal. ``succession-invariants`` used to email on a low chamber count;
once PR E disables it, the ``assignments_chamber_vacancy`` warning (and the seat-occupancy
ratchet's "ratchet me down") must reach the operator some other way. This stage reads the
build's ``run_results.json`` and fails the run, counted, on any node that did not pass.
"""

from __future__ import annotations

import json
import logging

import duckdb
import pytest

from clearinghouse_core.job import EXIT_DEGRADED, OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK
from clearinghouse_core.testing import patch_job_runtime
from usa_wa_pipeline.build_warnings import (
    ACCEPTED_WARNINGS,
    JOB_SLUG,
    AcceptedWarning,
    main,
    staged_sponsors,
    summarize,
)


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
    # Pinned off the #452 acceptance's biennium, so the clock cannot turn this into it.
    monkeypatch.setenv("USA_WA_BIENNIUM", "2025-26")
    path = tmp_path / "run_results.json"
    path.write_text(json.dumps(CLEAN))
    assert main(["--run-results", str(path)]) == 0

    warned = _results(("test.usa_wa_pipeline.assignments_chamber_vacancy", "warn"))
    path.write_text(json.dumps(warned))
    assert main(["--run-results", str(path)]) == 1


def test_a_missing_file_is_degraded(tmp_path, monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    assert main(["--run-results", str(tmp_path / "absent.json")]) == EXIT_DEGRADED


VACANCY = "test.usa_wa_pipeline.assignments_chamber_vacancy"
ROLLOVER = _results(("model.usa_wa_pipeline.assignments", "success"), (VACANCY, "warn"))


class TestAcceptedWarnings:
    """#452: from 2027-01-01 until WSL publishes 2027-28, every 2025-26 span has closed and
    nothing is open, so ``assignments_chamber_vacancy`` warns every night — an email nobody
    can act on (#333). Accepted while the current biennium has no staged sponsors; stale
    (degraded, naming itself) once sponsors stage, which forces its removal."""

    ACCEPT = (
        AcceptedWarning(
            node=VACANCY,
            biennium="2027-28",
            reason="unpublished",
            issue="#452",
            observed="2026-10-01",
            follow_up=("remove this entry",),
        ),
    )

    def test_the_rollover_vacancy_is_accepted(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            result = summarize(
                ROLLOVER, accepted=self.ACCEPT, biennium="2027-28", staged_sponsors=0
            )
        assert result.outcome == OUTCOME_OK
        assert result.counters["accepted_warnings"] == [VACANCY]
        assert result.counters["not_clean"] == []
        assert result.counters["warned"] == 1  # still legible, just unalarmed
        [record] = [r for r in caplog.records if r.message == "dbt_build_accepted_warning"]
        assert record.issues == ["#452"]

    def test_another_biennium_is_not_covered(self) -> None:
        """The biennium pin is the time box: dormant before 2027-01-01."""
        result = summarize(ROLLOVER, accepted=self.ACCEPT, biennium="2025-26", staged_sponsors=0)
        assert result.outcome == OUTCOME_FAILED
        assert result.counters["stale_acceptances"] == []

    def test_unknown_staged_sponsors_are_not_covered(self) -> None:
        result = summarize(ROLLOVER, accepted=self.ACCEPT, biennium="2027-28", staged_sponsors=None)
        assert result.outcome == OUTCOME_FAILED
        assert result.counters["stale_acceptances"] == []

    def test_only_the_named_node_is_covered(self) -> None:
        other = "test.usa_wa_pipeline.assignments_seat_occupancy"
        result = summarize(
            _results((VACANCY, "warn"), (other, "warn")),
            accepted=self.ACCEPT,
            biennium="2027-28",
            staged_sponsors=0,
        )
        assert result.outcome == OUTCOME_FAILED
        assert result.counters["not_clean"] == [other]

    def test_only_a_warning_is_covered_never_an_error(self) -> None:
        result = summarize(
            _results((VACANCY, "error")),
            accepted=self.ACCEPT,
            biennium="2027-28",
            staged_sponsors=0,
        )
        assert result.outcome == OUTCOME_FAILED
        assert result.counters["not_clean"] == [VACANCY]

    def test_staged_sponsors_make_the_acceptance_stale(self, caplog) -> None:
        """WSL published: degraded, with the whole cleanup on the line."""
        with caplog.at_level(logging.WARNING):
            result = summarize(CLEAN, accepted=self.ACCEPT, biennium="2027-28", staged_sponsors=88)
        assert result.outcome == OUTCOME_DEGRADED
        assert result.resolved_exit_code() == EXIT_DEGRADED
        assert result.counters["stale_acceptances"] == [VACANCY]
        [record] = [r for r in caplog.records if r.message == "dbt_build_acceptance_stale"]
        assert record.follow_up == ["remove this entry"]

    def test_a_partial_roster_still_warns_and_names_the_stale_entry(self) -> None:
        """Sponsors landed but the chambers have not filled: the vacancy is real again."""
        result = summarize(ROLLOVER, accepted=self.ACCEPT, biennium="2027-28", staged_sponsors=88)
        assert result.outcome == OUTCOME_FAILED
        assert result.resolved_exit_code() == 1
        assert result.counters["not_clean"] == [VACANCY]
        assert result.counters["stale_acceptances"] == [VACANCY]

    def test_the_shipped_acceptance_is_the_2027_rollover_vacancy(self) -> None:
        """Pins what is shipped, so widening it is a deliberate diff."""
        assert [(a.node, a.biennium, a.issue) for a in ACCEPTED_WARNINGS] == [
            (VACANCY, "2027-28", "#452")
        ]

    def test_the_shipped_follow_up_names_both_entries(self) -> None:
        [shipped] = ACCEPTED_WARNINGS
        assert any("ACCEPTED_WARNINGS" in step for step in shipped.follow_up)
        assert any("ACCEPTED_ROSTER_FAULTS" in step for step in shipped.follow_up)


def _db_with_sponsors(tmp_path, bienniums: list[str]):
    path = tmp_path / "pipeline.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create table stg_wsl_sponsors (biennium varchar, member_id varchar)")
    for i, biennium in enumerate(bienniums):
        con.execute("insert into stg_wsl_sponsors values (?, ?)", [biennium, str(i)])
    con.close()
    return path


class TestStagedSponsors:
    def test_counts_only_the_biennium_asked_about(self, tmp_path) -> None:
        db = _db_with_sponsors(tmp_path, ["2025-26", "2025-26", "2027-28"])
        assert staged_sponsors(db, "2025-26") == 2
        assert staged_sponsors(db, "2027-28") == 1
        assert staged_sponsors(db, "2029-30") == 0

    def test_a_missing_database_is_unknown(self, tmp_path) -> None:
        assert staged_sponsors(tmp_path / "absent.duckdb", "2027-28") is None

    def test_a_database_without_the_staging_table_is_unknown(self, tmp_path) -> None:
        path = tmp_path / "pipeline.duckdb"
        duckdb.connect(str(path)).close()
        assert staged_sponsors(path, "2027-28") is None


def test_main_accepts_the_rollover_vacancy_from_the_built_db(tmp_path, monkeypatch) -> None:
    """End to end: the current biennium and its staged sponsors come from the run itself."""
    patch_job_runtime(monkeypatch)
    monkeypatch.setenv("USA_WA_BIENNIUM", "2027-28")
    path = tmp_path / "run_results.json"
    path.write_text(json.dumps(ROLLOVER))
    empty = _db_with_sponsors(tmp_path, ["2025-26"])
    assert main(["--run-results", str(path), "--db", str(empty)]) == 0

    published = tmp_path / "published"
    published.mkdir()
    db = _db_with_sponsors(published, ["2027-28"])
    assert main(["--run-results", str(path), "--db", str(db)]) == 1


def test_main_reads_the_db_from_the_environment_by_default(tmp_path, monkeypatch) -> None:
    patch_job_runtime(monkeypatch)
    monkeypatch.setenv("USA_WA_BIENNIUM", "2027-28")
    monkeypatch.setenv("USA_WA_PIPELINE_DB", str(_db_with_sponsors(tmp_path, [])))
    path = tmp_path / "run_results.json"
    path.write_text(json.dumps(ROLLOVER))
    assert main(["--run-results", str(path)]) == 0
