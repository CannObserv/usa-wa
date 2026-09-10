"""The crosswalk's merge rule, shared by every conformed consumer (#366 CR 12)."""

import pandas as pd

from usa_wa_pipeline.conformed.crosswalk import merge_map, resolve_merged

ROWS = [
    {"entity_id": "01A", "natural_key": "src:1", "merged_into": "01B"},
    {"entity_id": "01B", "natural_key": "src:2", "merged_into": None},
]


def test_merge_map_carries_only_the_tombstones() -> None:
    assert merge_map(ROWS) == {"01A": "01B"}


def test_a_chain_resolves_to_the_terminal_survivor() -> None:
    rows = [{"entity_id": "01B", "natural_key": "src:2", "merged_into": "01C"}, *ROWS[:1]]
    merges = merge_map(rows)
    assert resolve_merged(merges, "01A") == "01C"


def test_a_pandas_null_is_not_a_tombstone() -> None:
    """The crosswalk models pin `merged_into` to pandas' `string` dtype, whose
    null is `pd.NA` — and the three consumers this replaces each screened for a
    different subset of pandas' nulls. `is not None` would have read every live
    row as merged into nothing; `str()` would have read it as merged into the
    entity `'<NA>'`. Screening on the type is total."""
    for null in (None, pd.NA, pd.NaT, float("nan")):
        assert merge_map([{"entity_id": "01A", "merged_into": null}]) == {}


def test_a_blank_is_not_a_tombstone() -> None:
    assert merge_map([{"entity_id": "01A", "merged_into": "  "}]) == {}
