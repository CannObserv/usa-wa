"""The roster↔WSL exact rule (#308) as a pure function (#302 CR: the logic
moved out of the dbt model so its edge cases are pytest-covered)."""

import pandas as pd

from usa_wa_pipeline.matching.roster_wsl import LINK_COLUMNS, ambiguous_folds, roster_wsl_links


def _roster(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["name", "year", "chamber", "district"])


def _sponsors(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["name", "long_name", "agency", "district", "biennium", "member_id"]
    )


SMITH_ROSTER = {"name": "Jordan Smith", "year": 2025, "chamber": "House", "district": "13"}
SMITH_SPONSOR = {
    "name": "Jordan Smith",
    "long_name": None,
    "agency": "House",
    "district": "13",
    "biennium": "2025-26",
    "member_id": "31000",
}


def test_seat_and_fold_agreement_links() -> None:
    out = roster_wsl_links(_roster([SMITH_ROSTER]), _sponsors([SMITH_SPONSOR]))
    assert list(out.columns) == LINK_COLUMNS
    [row] = out.to_dict("records")
    assert row["kind"] == "person"
    assert row["left_key"].startswith("usa_wa_legislature_roster:")
    assert row["left_key"].endswith(":2025")
    assert row["right_key"] == "usa_wa_legislature:31000"


def test_seat_disagreement_does_not_link() -> None:
    other_seat = {**SMITH_SPONSOR, "district": "14"}
    assert roster_wsl_links(_roster([SMITH_ROSTER]), _sponsors([other_seat])).empty


def test_null_named_sponsor_is_dropped_not_crashed() -> None:
    """A sponsor row with neither name nor long_name must not fail the build
    (CR 8): Series.map would hand identity_fold a NaN and raise."""
    nameless = {**SMITH_SPONSOR, "name": None, "long_name": None, "member_id": "31001"}
    out = roster_wsl_links(_roster([SMITH_ROSTER]), _sponsors([SMITH_SPONSOR, nameless]))
    assert len(out) == 1
    assert out.iloc[0]["right_key"] == "usa_wa_legislature:31000"


def test_long_name_fallback_still_links() -> None:
    fallback = {**SMITH_SPONSOR, "name": None, "long_name": "Jordan Smith"}
    assert len(roster_wsl_links(_roster([SMITH_ROSTER]), _sponsors([fallback]))) == 1


def test_wide_gap_fold_is_withheld_entirely() -> None:
    """The Jr/Sr signature (CR 9): a fold whose roster years gap wider than the
    adapter's WIDE_GAP_YEARS must propose nothing — min(year) would hand the
    younger member the elder's seeded key, a silent cross-person merge."""
    elder = {"name": "Jordan Smith", "year": 1955, "chamber": "House", "district": "13"}
    roster = _roster([elder, SMITH_ROSTER])
    assert ambiguous_folds(roster.assign(fold=["jordansmith", "jordansmith"])) == {"jordansmith"}
    out = roster_wsl_links(roster, _sponsors([SMITH_SPONSOR]))
    assert out.empty


def test_continuous_service_is_not_ambiguous() -> None:
    """A genuine long tenure (no wide gap) keys on the true first year."""
    rows = [
        {"name": "Jordan Smith", "year": y, "chamber": "House", "district": "13"}
        for y in range(1989, 2026, 2)
    ]
    out = roster_wsl_links(_roster(rows), _sponsors([SMITH_SPONSOR]))
    [row] = out.to_dict("records")
    assert row["left_key"].endswith(":1989")


def test_empty_inputs_return_typed_empty_frame() -> None:
    out = roster_wsl_links(_roster([]), _sponsors([SMITH_SPONSOR]))
    assert out.empty and list(out.columns) == LINK_COLUMNS
