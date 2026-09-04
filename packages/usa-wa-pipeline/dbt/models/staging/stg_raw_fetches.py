"""stg_raw_fetches: thin dbt adapter over usa_wa_pipeline.staging.fetches (#313).

The attestation dimension every citation joins through. Reads the raw ROOT, not
one source's slice — the sources are discovered, so a source added to the
harvest chain is covered without editing anything here.
"""

import pandas as pd

from clearinghouse_core.rawstore import get_raw_root
from usa_wa_pipeline.staging import fetches


def model(dbt, session):
    dbt.config(materialized="table")
    rows = fetches.fetch_rows(get_raw_root())
    return pd.DataFrame(rows, columns=fetches.FETCH_COLUMNS)
