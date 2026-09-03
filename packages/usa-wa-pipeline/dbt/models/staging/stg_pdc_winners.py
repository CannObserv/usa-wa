"""stg_pdc_winners: thin dbt adapter over usa_wa_pipeline.staging.pdc.winner_rows (#307).

Logic + tests live in the Python package (docs/PIPELINE.md § TDD for dbt
models); this file only binds the raw store to a DataFrame.
"""

import pandas as pd

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_pipeline.staging import pdc


def model(dbt, session):
    dbt.config(materialized="table")
    rows = pdc.winner_rows(RawStore(get_raw_root(), "usa_wa_pdc"))
    return pd.DataFrame(rows, columns=pdc.WINNER_COLUMNS)
