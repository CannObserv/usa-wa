"""role_periods (#501 D1): each role's seat capacity, one row per period.

Thin adapter over usa_wa_pipeline.conformed.capacity.role_period_rows: dense
across every held role, tiled from the floor of its earliest tenure to an open
last period. The counterpart clip in `assignments` reads the same capacity
(`seat_capacity`), so the clip and the occupancy gate read one statement.
"""

from usa_wa_pipeline.conformed.capacity import ROLE_PERIOD_SCHEMA, role_period_rows
from usa_wa_pipeline.frames import typed_relation


def model(dbt, session):
    dbt.config(materialized="table")
    rows = role_period_rows(
        dbt.ref("assignments").df().to_dict("records"),
        dbt.ref("stg_roster_members").df().to_dict("records"),
    )
    return typed_relation(session, rows, ROLE_PERIOD_SCHEMA)
