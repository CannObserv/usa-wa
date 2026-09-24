"""persons (#309): one row per live registry entity, survivorship applied.

Thin adapter over usa_wa_pipeline.conformed.entities.person_rows
(roster > WSL > PDC name precedence; logic + tests in the package).
"""

from usa_wa_pipeline.conformed.entities import PERSON_SCHEMA, person_rows
from usa_wa_pipeline.frames import typed_relation


def model(dbt, session):
    dbt.config(materialized="table")
    crosswalk = dbt.ref("person_crosswalk").df().to_dict("records")
    rows = person_rows(
        crosswalk,
        sponsors=dbt.ref("stg_wsl_sponsors").df().to_dict("records"),
        roster=dbt.ref("stg_roster_members").df().to_dict("records"),
        pdc=dbt.ref("stg_pdc_winners").df().to_dict("records"),
    )
    return typed_relation(session, rows, PERSON_SCHEMA)
