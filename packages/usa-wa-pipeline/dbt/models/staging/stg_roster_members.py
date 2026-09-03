"""stg_roster_members: thin dbt adapter over usa_wa_pipeline.staging.roster (#306).

Parses the newest archived roster-PDF revision through the adapter's real
extraction; logic + tests live in the Python package.
"""

import pandas as pd

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_pipeline.staging import roster


def model(dbt, session):
    dbt.config(materialized="table")
    rows = roster.roster_rows(RawStore(get_raw_root(), "usa_wa_legislature_roster"))
    return pd.DataFrame(rows, columns=roster.ROSTER_COLUMNS)
