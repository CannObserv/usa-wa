"""seat_winners (#412 PR B): each seat a ballot decided, keyed as assignments key it.

Thin binder over the pure, pytest-covered `usa_wa_pipeline.conformed.winners`
(docs/PIPELINE.md § TDD policy). Internal: read by the odd-year corroboration
test, never published.
"""

from usa_wa_pipeline.conformed.winners import SEAT_WINNER_SCHEMA, winner_rows
from usa_wa_pipeline.frames import typed_relation


def model(dbt, session):
    dbt.config(materialized="table")
    rows = winner_rows(dbt.ref("stg_sos_results").df().to_dict("records"))
    return typed_relation(session, rows, SEAT_WINNER_SCHEMA)
