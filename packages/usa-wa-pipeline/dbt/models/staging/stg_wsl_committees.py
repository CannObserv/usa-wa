"""stg_wsl_committees: thin dbt adapter over usa_wa_pipeline.staging.wsl.committee_rows (#306).

Logic + tests live in the Python package (docs/PIPELINE.md § TDD for dbt
models); this file only binds the raw store to a DataFrame.
"""

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG
from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.staging import wsl


def model(dbt, session):
    dbt.config(materialized="table")
    rows = wsl.committee_rows(RawStore(get_raw_root(), WSL_SOURCE_SLUG))
    return typed_relation(session, rows, wsl.COMMITTEE_SCHEMA)
