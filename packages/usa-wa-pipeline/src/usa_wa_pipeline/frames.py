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
"""

from collections.abc import Iterable, Mapping
from typing import Any

import duckdb
import pandas as pd

#: Ordered column → duckdb type. Order is the published column order.
Schema = Mapping[str, str]


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
    projection = ", ".join(
        f'cast("{name}" as {type_}) as "{name}"' for name, type_ in schema.items()
    )
    return session.from_df(frame).project(projection)
