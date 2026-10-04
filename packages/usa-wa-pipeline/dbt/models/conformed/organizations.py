"""organizations (#309): one row per live registry entity.

Thin adapter over usa_wa_pipeline.conformed.entities.org_rows (newest
biennium's committee attributes; meeting-derived fallback for Joint/Other;
``active`` on the assignments model's clock, #428 — and false for a predecessor
org_lineage names as succeeded or merged, #447).
"""

from usa_wa_pipeline.conformed.entities import ORG_SCHEMA, org_rows
from usa_wa_pipeline.conformed.lineage import retired_entities
from usa_wa_pipeline.conformed.spans import current_biennium
from usa_wa_pipeline.frames import typed_relation


def model(dbt, session):
    dbt.config(materialized="table")
    crosswalk = dbt.ref("org_crosswalk").df().to_dict("records")
    rows = org_rows(
        crosswalk,
        committees=dbt.ref("stg_wsl_committees").df().to_dict("records"),
        meetings=dbt.ref("stg_wsl_meetings").df().to_dict("records"),
        current_biennium=current_biennium(),
        retired=retired_entities(dbt.ref("org_lineage").df().to_dict("records")),
    )
    return typed_relation(session, rows, ORG_SCHEMA)
