"""The registrar (#308): proposed link pairs → clusters → registry writes."""

import duckdb
import pytest

from clearinghouse_core.registry import KIND_PERSON, apply_decision, decide, registered_view
from usa_wa_pipeline.registrar import cluster_pairs, load_pairs, run_registrar, unprocessed_kinds


def test_cluster_pairs_connected_components() -> None:
    clusters = cluster_pairs(
        [("a", "b"), ("b", "c"), ("x", "y"), ("solo", "solo")],
    )
    assert sorted(sorted(c) for c in clusters) == [["a", "b", "c"], ["solo"], ["x", "y"]]


@pytest.mark.db
async def test_registrar_mints_appends_and_reports_conflicts(db_session) -> None:
    # pre-register two distinct entities the conflict pair will collide across
    a = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:1"}), {}), registered_by="test"
    )
    b = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:2"}), {}), registered_by="test"
    )

    summary = await run_registrar(
        db_session,
        KIND_PERSON,
        pairs=[
            ("wsl:1", "wa_pdc:100"),  # append onto a
            ("new:1", "new:2"),  # mint
            ("wsl:1", "wsl:2"),  # conflict: a vs b
        ],
    )
    assert summary["appended_clusters"] == 0  # the conflict swallowed wsl:1's component
    assert summary["conflicts"] == 1
    view = await registered_view(db_session, KIND_PERSON)
    # conflict wrote nothing: the pdc key rode the conflicted component
    assert "wa_pdc:100" not in view
    assert view["wsl:1"] == a
    assert view["wsl:2"] == b
    assert view["new:1"] == view["new:2"]
    assert summary["minted"] == 1


@pytest.mark.db
async def test_registrar_clean_append(db_session) -> None:
    a = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:1"}), {}), registered_by="test"
    )
    summary = await run_registrar(db_session, KIND_PERSON, pairs=[("wsl:1", "wa_pdc:100")])
    assert summary["appended_clusters"] == 1
    assert (await registered_view(db_session, KIND_PERSON))["wa_pdc:100"] == a


@pytest.mark.db
async def test_registrar_idempotent(db_session) -> None:
    await run_registrar(db_session, KIND_PERSON, pairs=[("a:1", "b:2")])
    summary = await run_registrar(db_session, KIND_PERSON, pairs=[("a:1", "b:2")])
    assert summary["minted"] == 0
    assert summary["noops"] == 1


def test_load_pairs_filters_on_kind(tmp_path) -> None:
    """CR 20: an org rule unioned into proposed_links must never reach the
    person registration path — load_pairs reads one kind only."""
    db_path = str(tmp_path / "m.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        "create table proposed_links as select * from (values "
        "('person', 'a', 'b', 'r', 1.0), ('org', 'x', 'y', 'r', 1.0)"
        ") t(kind, left_key, right_key, rule, score)"
    )
    con.close()
    assert load_pairs(db_path) == [("a", "b")]
    assert load_pairs(db_path, "org") == [("x", "y")]


def test_unprocessed_kinds_names_what_the_job_skips(tmp_path) -> None:
    """CR 40: pairs of a kind the job does not register must degrade, not
    vanish silently."""
    db_path = str(tmp_path / "m.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        "create table proposed_links as select * from (values "
        "('person', 'a', 'b', 'r', 1.0), ('org', 'x', 'y', 'r', 1.0)"
        ") t(kind, left_key, right_key, rule, score)"
    )
    con.close()
    assert unprocessed_kinds(db_path) == ["org"]

    con = duckdb.connect(str(tmp_path / "p.duckdb"))
    con.execute(
        "create table proposed_links as select * from (values "
        "('person', 'a', 'b', 'r', 1.0)) t(kind, left_key, right_key, rule, score)"
    )
    con.close()
    assert unprocessed_kinds(str(tmp_path / "p.duckdb")) == []


def test_unprocessed_kinds_tolerates_a_null_kind(tmp_path) -> None:
    """CR 53: a NULL kind must be reported, not crash the sort (the dbt
    not_null test guards the nightly by ordering, but this is callable alone)."""
    db_path = str(tmp_path / "n.duckdb")
    con = duckdb.connect(db_path)
    con.execute(
        "create table proposed_links as select * from (values "
        "('person', 'a', 'b', 'r', 1.0), (NULL, 'x', 'y', 'r', 1.0), ('org', 'q', 'z', 'r', 1.0)"
        ") t(kind, left_key, right_key, rule, score)"
    )
    con.close()
    assert unprocessed_kinds(db_path) == ["<null>", "org"]
