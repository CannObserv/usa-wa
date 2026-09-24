"""Typed materialization for dbt Python models (#361).

A model that returns a bare ``pd.DataFrame`` leaves its column types to duckdb's
inference, and duckdb reads an ``object`` column with no values as ``INTEGER``.
That is every column of an empty model — the hermetic build's whole corpus, and
in production any model whose source is empty today (``stg_sos_filings`` shipped
all-INTEGER for weeks) — and any populated column that happens to be all NULL.
SQL that is correct against real types then fails to bind, or binds and means
something else, and the published datapackage advertises the wrong types.

So every model declares its schema — ordered column → duckdb type — beside the
row builder that fills it, and returns :func:`typed_relation` rather than a
frame. The type becomes a property of the model, not of the rows it happened to
read. pandas cannot carry the declaration itself: it has no DATE dtype, so an
empty date column would still come out as something else.

The cast TYPES; it never converts. A cast that turned float64 ``10.0`` into the
text ``'10.0'``, or rounded ``10.5`` into ``10``, would let a source drifting type
publish silently — before, that drift moved the published type and the
publisher's contract gate refused it. So a column is cast only when its inferred
type already is the declared one, is a lossless widening of it, or is a guess
(the column is all NULL); anything else is refused, naming the column.
"""

from collections.abc import Iterable, Mapping
from typing import Any

import duckdb
import pandas as pd

#: Ordered column → duckdb type. Order is the published column order.
Schema = Mapping[str, str]

#: (inferred, declared) pairs that cast with no value able to change.
_WIDENINGS = frozenset({("INTEGER", "BIGINT"), ("INTEGER", "DOUBLE"), ("BIGINT", "DOUBLE")})


def typed_relation(
    session: duckdb.DuckDBPyConnection,
    rows: Iterable[Mapping[str, Any]] | pd.DataFrame,
    schema: Schema,
) -> duckdb.DuckDBPyRelation:
    """``rows`` as a relation whose columns are exactly ``schema``, cast to its types.

    ``rows`` is row dicts (missing keys read NULL, as ``pd.DataFrame`` has always
    treated them) or a frame, whose columns must already be the schema's, in
    order: a model emitting some other shape is a bug to surface, not coerce.
    """
    columns = list(schema)
    if isinstance(rows, pd.DataFrame):
        frame = rows
        if list(frame.columns) != columns:
            raise ValueError(f"frame columns {list(frame.columns)} != schema columns {columns}")
    else:
        frame = pd.DataFrame(list(rows), columns=columns)
    relation = session.from_df(frame)
    inferred = dict(zip(relation.columns, (str(t) for t in relation.types), strict=True))
    for name, declared in schema.items():
        _refuse_a_conversion(relation, name, inferred[name], declared)
    projection = ", ".join(
        f'cast("{name}" as {type_}) as "{name}"' for name, type_ in schema.items()
    )
    return relation.project(projection)


def _refuse_a_conversion(
    relation: duckdb.DuckDBPyRelation, name: str, inferred: str, declared: str
) -> None:
    """Raise unless casting ``name`` from ``inferred`` to ``declared`` keeps every value.

    The one narrowing allowed is DOUBLE → BIGINT over whole numbers: a single NULL
    makes pandas widen an integer column to float64, which is how ``roles.district``
    arrives (#361 CR 1).
    """
    if inferred == declared or (inferred, declared) in _WIDENINGS:
        return
    column = f'"{name}"'
    if relation.aggregate(f"count({column})").fetchone()[0] == 0:
        return  # all NULL: the inferred type is duckdb's guess, not the data's
    if inferred == "DOUBLE" and declared == "BIGINT":
        fractional = f"count(*) filter (where {column} <> trunc({column}))"
        if relation.aggregate(fractional).fetchone()[0] == 0:
            return
    raise ValueError(
        f"column {name!r}: its values infer {inferred}, and casting them to the declared "
        f"{declared} would convert rather than type them"
    )
