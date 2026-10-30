"""The multi-person folds probe (#548).

The roster carries no member ids, so one identity is one fold, modulo the
adjudication tables. Two people sharing a fold publish as one entity, and only a
manual sweep found the two there were (#525: ``juriebsmith``, ``tombrown``). The
probe is that sweep, nightly, over the identities the resolver actually mints —
so an adjudicated split no longer hits, and the accepted list starts empty.
"""

import duckdb
import pandas as pd
import pytest

from clearinghouse_core.job import EXIT_DEGRADED, OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK
from clearinghouse_core.testing import patch_job_runtime
from usa_wa_pipeline.multi_person_folds import (
    ACCEPTED,
    SHAPE_AFTER_DEATH,
    SHAPE_MIXED_HONORIFIC,
    Acceptance,
    find_hits,
    load_roster,
    main,
    run_probe,
)
from usa_wa_pipeline.staging.roster import ROSTER_SCHEMA


def _row(
    name: str,
    year: int,
    *,
    chamber: str = "house",
    district: int = 53,
    annotation: str | None = None,
) -> dict:
    return {
        "district": district,
        "chamber": chamber,
        "year": year,
        "order": 1,
        "name": name,
        "party_token": "R",
        "annotation": annotation,
    }


#: A death the corpus needs anyway: the probe degrades on a roster with none.
DEATH = _row("Ada Other", 1951, district=9, annotation="Deceased June 2, 1952")

BROWN = [
    *(_row("Tom Brown", year) for year in (1915, 1917, 1919)),
    _row("Tom Brown", 1921, annotation="Deceased March 27, 1921"),
]


def _hits(rows: list[dict]) -> dict[tuple[str, str], tuple[str, ...]]:
    hits, _counters = find_hits(rows, splits={})
    return {(h.key, h.shape): h.evidence for h in hits}


# --- rows after a death ------------------------------------------------------


def test_a_row_listed_after_the_death_in_one_identity_is_a_hit():
    """tombrown before #548: the LD-53 Republican's death, then a 1937 LD-25 row."""
    hits = _hits([*BROWN, _row("Tom Brown", 1937, district=25)])
    assert list(hits) == [("tombrown:1915", SHAPE_AFTER_DEATH)]
    evidence = hits[("tombrown:1915", SHAPE_AFTER_DEATH)]
    assert any("1921" in line and "Deceased March 27, 1921" in line for line in evidence)
    assert any(line.startswith("1937 house LD25") for line in evidence)


def test_the_death_row_and_rows_before_it_are_not_hits():
    assert _hits([*BROWN, DEATH]) == {}


def test_a_row_listed_the_year_after_a_death_dated_into_it_is_not_a_hit():
    """Smith's shape: re-elected, died before the term, listed under the new year
    with the earlier date. His own row is the death row, never a hit."""
    rows = [
        _row("Jurie B. Smith", 1941, district=32),
        _row("Jurie B. Smith", 1943, district=32, annotation="Deceased November 17, 1942"),
    ]
    assert _hits(rows) == {}


def test_a_row_in_the_year_of_the_death_is_not_certainly_after_it():
    """Rows index by term-start year: a 1921 row may be the term he died in."""
    rows = [*BROWN, _row("Tom Brown", 1921, chamber="senate")]
    assert _hits(rows) == {}


def test_a_death_dated_only_to_its_year_bounds_at_that_years_end():
    rows = [
        _row("Tom Brown", 1919, annotation="Deceased 1919"),
        _row("Tom Brown", 1921),
    ]
    assert list(_hits(rows)) == [("tombrown:1919", SHAPE_AFTER_DEATH)]


def test_an_undated_death_bounds_at_its_terms_end():
    """No date stated: he died during the row's term, so only a row past that term is
    certainly after. A Senate term is four years."""
    rows = [
        _row("Tom Brown", 1915, chamber="senate", annotation="Deceased"),
        _row("Tom Brown", 1917, chamber="senate"),
    ]
    assert _hits(rows) == {}
    rows.append(_row("Tom Brown", 1919))
    assert list(_hits(rows)) == [("tombrown:1915", SHAPE_AFTER_DEATH)]


def test_the_check_reads_the_parsed_clause_not_the_wording():
    """ "Died" parses to the same verb as "Deceased"; a death with other clauses still
    reads (ARCHITECTURE.md's second rule)."""
    rows = [
        _row("Tom Brown", 1915, annotation="Appointed Jan. 9, 1915; Died March 27, 1916"),
        _row("Tom Brown", 1917),
    ]
    assert list(_hits(rows)) == [("tombrown:1915", SHAPE_AFTER_DEATH)]


def test_an_adjudicated_split_is_two_identities_and_no_hit():
    """Over minted identities, not raw folds: #548's split clears its own hit."""
    rows = [*BROWN, _row("Tom Brown", 1937, district=25)]
    hits, _ = find_hits(rows, splits={"tombrown": 1937})
    assert hits == []


def test_the_shipped_split_tables_are_the_default():
    rows = [*BROWN, _row("Tom Brown", 1937, district=25), DEATH]
    hits, counters = find_hits(rows)
    assert hits == []
    assert counters["identities"] == 3


# --- the widow shape ---------------------------------------------------------


SMITH = [
    _row("Jurie B. Smith", 1941, district=32),
    _row("Jurie B. Smith", 1943, district=32, annotation="Deceased November 17, 1942"),
    _row("Mrs. Jurie B. Smith", 1943, district=32, annotation="Appointed to serve unexpired term"),
    _row("Mrs. Jurie B.(Nettie Luella) Smith", 1945, district=32),
]


def test_a_fold_mixing_leading_mrs_rows_with_others_is_a_hit():
    """juriebsmith before #525. It crosses the death too: both shapes report."""
    hits, _ = find_hits(SMITH, splits={}, widow_splits={})
    assert {(h.key, h.shape) for h in hits} == {
        ("juriebsmith:1941", SHAPE_AFTER_DEATH),
        ("juriebsmith:1941", SHAPE_MIXED_HONORIFIC),
    }


def test_a_widow_printed_without_a_death_still_hits_on_the_honorific():
    rows = [r for r in SMITH if not r["annotation"]]
    hits, _ = find_hits(rows, splits={}, widow_splits={})
    assert [(h.key, h.shape) for h in hits] == [("juriebsmith:1941", SHAPE_MIXED_HONORIFIC)]


def test_a_fold_of_mrs_rows_only_is_one_woman_and_no_hit():
    rows = [_row("Mrs. Frances Swayze", 1953), _row("Mrs. Frances Swayze", 1955)]
    assert _hits(rows) == {}


def test_the_widow_split_clears_both_hits():
    hits, _ = find_hits(SMITH, splits={}, widow_splits={"juriebsmith": "#525"})
    assert hits == []


# --- the gate ----------------------------------------------------------------


TWO_BROWNS = [*BROWN, _row("Tom Brown", 1937, district=25)]


def test_an_unaccepted_hit_fails_and_names_the_identity():
    result = run_probe(TWO_BROWNS, splits={}, accepted=())
    assert result.outcome == OUTCOME_FAILED
    assert [(h["key"], h["shape"]) for h in result.counters["unaccepted_hits"]] == [
        ("tombrown:1915", SHAPE_AFTER_DEATH)
    ]
    assert result.counters["failures"] == ["unaccepted_hits"]


def test_an_accepted_hit_passes_and_is_reported():
    accepted = (Acceptance("tombrown:1915", SHAPE_AFTER_DEATH, "judged one man", "p. 1"),)
    result = run_probe(TWO_BROWNS, splits={}, accepted=accepted)
    assert result.outcome == OUTCOME_OK
    assert result.counters["accepted"] == ["tombrown:1915 after_death"]


def test_a_stale_acceptance_fails():
    accepted = (Acceptance("tombrown:1915", SHAPE_AFTER_DEATH, "judged one man", "p. 1"),)
    result = run_probe([*BROWN, DEATH], splits={}, accepted=accepted)
    assert result.outcome == OUTCOME_FAILED
    assert result.counters["stale_acceptances"] == ["tombrown:1915 after_death"]


def test_a_clean_run_counts_what_it_compared():
    result = run_probe([*BROWN, DEATH], splits={}, accepted=())
    assert result.outcome == OUTCOME_OK
    assert result.counters["identities"] == 2
    assert result.counters["death_identities"] == 2
    assert result.counters["hits"] == 0


@pytest.mark.parametrize(
    ("roster", "reason"),
    [
        ([], "empty_roster_rows"),
        # Every death unparsed: an upstream rewording, which would read as "no hits".
        ([_row("Tom Brown", 1915), _row("Tom Brown", 1917)], "no_death_clauses"),
    ],
)
def test_a_blind_input_degrades_rather_than_passing(roster, reason):
    result = run_probe(roster, accepted=())
    assert result.outcome == OUTCOME_DEGRADED
    assert result.counters == {reason: True}


def test_the_accepted_list_starts_empty():
    """Both known folds are split (#525, #548), so nothing is suppressed: the next
    multi-person fold fails with no entry to hide behind."""
    assert ACCEPTED == ()


# --- the job -----------------------------------------------------------------


def _pipeline_db(tmp_path, roster) -> str:
    db_path = str(tmp_path / "pipeline.duckdb")
    con = duckdb.connect(db_path)
    columns = ", ".join(f'"{name}" {type_}' for name, type_ in ROSTER_SCHEMA.items())
    con.execute(f"create table stg_roster_members ({columns})")
    if roster:
        frame = pd.DataFrame([{name: row.get(name) for name in ROSTER_SCHEMA} for row in roster])
        con.register("frame", frame)
        con.execute("insert into stg_roster_members select * from frame")
    con.close()
    return db_path


def test_load_roster_reads_the_staged_roster(tmp_path):
    rows = load_roster(_pipeline_db(tmp_path, BROWN))
    assert [r["year"] for r in rows] == [1915, 1917, 1919, 1921]


def test_main_reads_the_build_and_exits_by_outcome(tmp_path, monkeypatch):
    patch_job_runtime(monkeypatch)
    assert main(["--db", _pipeline_db(tmp_path, [*BROWN, DEATH])]) == 0


def test_main_reads_the_db_from_the_environment_by_default(tmp_path, monkeypatch):
    patch_job_runtime(monkeypatch)
    (tmp_path / "e").mkdir()
    monkeypatch.setenv("USA_WA_PIPELINE_DB", _pipeline_db(tmp_path / "e", []))
    assert main([]) == EXIT_DEGRADED


def test_main_fails_on_a_hit(tmp_path, monkeypatch):
    """The shipped tables split tombrown, so a namesake under a new fold stands in."""
    patch_job_runtime(monkeypatch)
    rows = [
        _row("Ann Roe", 1915, annotation="Deceased March 27, 1916"),
        _row("Ann Roe", 1937, district=25),
    ]
    (tmp_path / "h").mkdir()
    assert main(["--db", _pipeline_db(tmp_path / "h", rows)]) == 1
