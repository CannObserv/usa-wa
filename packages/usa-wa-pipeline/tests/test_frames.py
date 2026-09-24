"""A dbt Python model's output carries its declared types, rows or none (#361).

duckdb infers a pandas ``object`` column's type from its values, so a column
with no values — an empty model, or a populated one whose column is all NULL —
comes out ``INTEGER``. `typed_relation` casts to a declared schema instead, so
the type is a property of the model rather than of whatever rows it happened
to read.
"""

from datetime import date

import duckdb
import pandas as pd
import pytest

from usa_wa_pipeline.frames import typed_relation

SCHEMA = {
    "entity_id": "VARCHAR",
    "valid_from": "DATE",
    "district": "BIGINT",
    "score": "DOUBLE",
    "is_active": "BOOLEAN",
    "order": "BIGINT",
}


@pytest.fixture
def session():
    con = duckdb.connect(":memory:")
    yield con
    con.close()


def _types(relation) -> dict[str, str]:
    return dict(zip(relation.columns, (str(t) for t in relation.types), strict=True))


def test_no_rows_still_carry_the_declared_types(session) -> None:
    assert _types(typed_relation(session, [], SCHEMA)) == SCHEMA


def test_an_all_null_column_carries_its_declared_type(session) -> None:
    rows = [
        {
            "entity_id": None,
            "valid_from": None,
            "district": None,
            "score": None,
            "is_active": None,
            "order": None,
        }
    ]
    assert _types(typed_relation(session, rows, SCHEMA)) == SCHEMA


def test_values_survive_the_cast(session) -> None:
    rows = [
        {
            "entity_id": "01A",
            "valid_from": date(2019, 7, 1),
            "district": 10,
            "score": 0.5,
            "is_active": True,
            "order": 2,
        },
        {
            "entity_id": "01B",
            "valid_from": None,
            "district": None,
            "score": None,
            "is_active": False,
            "order": 3,
        },
    ]
    assert typed_relation(session, rows, SCHEMA).fetchall() == [
        ("01A", date(2019, 7, 1), 10, 0.5, True, 2),
        ("01B", None, None, None, False, 3),
    ]


def test_a_frame_is_accepted(session) -> None:
    frame = pd.DataFrame(columns=list(SCHEMA))
    assert _types(typed_relation(session, frame, SCHEMA)) == SCHEMA


def test_a_frame_whose_columns_disagree_with_the_schema_is_refused(session) -> None:
    """The schema is the contract: a model emitting other columns, or the same
    ones in another order, is a bug to surface, not a shape to coerce."""
    frame = pd.DataFrame(columns=list(reversed(SCHEMA)))
    with pytest.raises(ValueError, match="columns"):
        typed_relation(session, frame, SCHEMA)


def test_a_number_is_never_rendered_as_text(session) -> None:
    """The cast TYPES, it does not convert (CR 1). A null among integers makes
    pandas widen them to float64, and casting that to VARCHAR publishes `'10.0'`
    for LD 10 — a source drifting type would ship silently instead of moving the
    contract the publisher gates on."""
    rows = [{"district": 10}, {"district": None}]
    with pytest.raises(ValueError, match="district"):
        typed_relation(session, rows, {"district": "VARCHAR"})


def test_a_fraction_is_never_rounded_into_an_integer(session) -> None:
    rows = [{"district": 10.5}, {"district": None}]
    with pytest.raises(ValueError, match="district"):
        typed_relation(session, rows, {"district": "BIGINT"})


def test_integers_widen_losslessly(session) -> None:
    """The float64 a null forces onto whole numbers narrows back to BIGINT — the
    `roles.district` case — and an integer column widens to DOUBLE."""
    rows = [{"district": 10, "score": 1}, {"district": None, "score": 2}]
    relation = typed_relation(session, rows, {"district": "BIGINT", "score": "DOUBLE"})
    assert relation.fetchall() == [(10, 1.0), (None, 2.0)]
