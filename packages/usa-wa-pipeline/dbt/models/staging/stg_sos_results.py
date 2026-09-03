"""stg_sos_results: thin dbt adapter over usa_wa_pipeline.staging.sos.result_rows (#307).

Logic + tests live in the Python package (docs/PIPELINE.md § TDD for dbt
models); this file only binds the raw store to a DataFrame.
"""

import pandas as pd

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_pipeline.staging import sos


def model(dbt, session):
    dbt.config(materialized="table")
    rows = sos.result_rows(RawStore(get_raw_root(), "usa_wa_sos_results"))
    return pd.DataFrame(rows, columns=sos.RESULT_COLUMNS)
