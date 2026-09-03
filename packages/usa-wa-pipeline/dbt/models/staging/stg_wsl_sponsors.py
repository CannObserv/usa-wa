"""stg_wsl_sponsors: thin dbt adapter over usa_wa_pipeline.staging.wsl.sponsor_rows (#306).

Logic + tests live in the Python package (docs/PIPELINE.md § TDD for dbt
models); this file only binds the raw store to a DataFrame.
"""

import pandas as pd

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_pipeline.staging import wsl


def model(dbt, session):
    dbt.config(materialized="table")
    rows = wsl.sponsor_rows(RawStore(get_raw_root(), "usa_wa_legislature"))
    return pd.DataFrame(rows, columns=wsl.SPONSOR_COLUMNS)
