"""organizations (#309): one row per live registry entity.

Thin adapter over usa_wa_pipeline.conformed.entities.org_rows (newest
biennium's committee attributes; meeting-derived fallback for Joint/Other).
"""

import pandas as pd

from usa_wa_pipeline.conformed.entities import ORG_COLUMNS, org_rows


def model(dbt, session):
    dbt.config(materialized="table")
    crosswalk = dbt.ref("org_crosswalk").df().to_dict("records")
    rows = org_rows(
        crosswalk,
        committees=dbt.ref("stg_wsl_committees").df().to_dict("records"),
        meetings=dbt.ref("stg_wsl_meetings").df().to_dict("records"),
    )
    return pd.DataFrame(rows, columns=ORG_COLUMNS)
