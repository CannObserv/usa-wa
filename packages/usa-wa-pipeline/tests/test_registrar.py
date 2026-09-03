"""The registrar (#308): proposed link pairs → clusters → registry writes."""

import pytest

from clearinghouse_core.registry import KIND_PERSON, registered_view
from usa_wa_pipeline.registrar import cluster_pairs, run_registrar


def test_cluster_pairs_connected_components() -> None:
    clusters = cluster_pairs(
        [("a", "b"), ("b", "c"), ("x", "y"), ("solo", "solo")],
    )
    assert sorted(sorted(c) for c in clusters) == [["a", "b", "c"], ["solo"], ["x", "y"]]


@pytest.mark.db
async def test_registrar_mints_appends_and_reports_conflicts(db_session) -> None:
    from clearinghouse_core.registry import apply_decision, decide

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
    from clearinghouse_core.registry import apply_decision, decide

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
