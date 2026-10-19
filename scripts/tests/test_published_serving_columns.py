"""The publisher's columns and the serving tables' columns are one contract.

`usa_wa_api.serving.load.verify_contract` already refuses a mismatch — in both
directions, deliberately: a declared field with no column would be silently
dropped, and a column no field fills would answer nulls for a value the dataset
used to carry. But it refuses at **chain time**, in the nightly's serving-load
step, long after the gate that was supposed to catch it.

usa-wa#370 walked straight into that gap. Adding `span_key` to `assignments`
passed 3,337 tests and `dbt build`, and would have failed the chain. This test
moves the same assertion to the gate, where a producer-side column addition is
one edit away from its consumer-side counterpart.

**`assignments` and `persons`, and for a reason** (CR 18). The other served
datasets' column lists live in dbt models rather than Python constants, so there
is nothing here to compare them against — this rail is as wide as the producer's
Python surface, not as wide as the contract. The runtime check in
`verify_contract` still covers all of them; what it does not do is fail early.
`persons` joined in #501, whose lockstep moves both datasets at once.

Cross-package by nature (pipeline ⟷ api), which is why it lives here rather than
in either package's own suite.
"""

import pytest

from usa_wa_api.serving.schema import SERVING_TABLES
from usa_wa_pipeline.conformed.entities import PERSON_COLUMNS
from usa_wa_pipeline.conformed.spans import ASSIGNMENT_COLUMNS


@pytest.mark.parametrize(
    ("name", "columns"), [("assignments", ASSIGNMENT_COLUMNS), ("persons", PERSON_COLUMNS)]
)
def test_the_dataset_publishes_exactly_what_serving_models(name: str, columns: list) -> None:
    served = set(SERVING_TABLES[name].columns.keys())
    published = set(columns)

    assert published == served, (
        f"conformed `{name}` and `serving.{name}` disagree on columns — "
        f"published-only {sorted(published - served)}, served-only {sorted(served - published)}. "
        "The nightly chain's serving load refuses this (verify_contract); fix it here."
    )
