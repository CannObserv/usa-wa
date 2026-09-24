"""stg_wsl_committee_members: thin adapter over staging.wsl.committee_member_rows (#306).

Logic + tests live in the Python package (docs/PIPELINE.md § TDD for dbt
models); this file only binds the raw store to a DataFrame.
"""

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.staging import wsl


def model(dbt, session):
    dbt.config(materialized="table")
    rows = wsl.committee_member_rows(RawStore(get_raw_root(), "usa_wa_legislature"))
    return typed_relation(session, rows, wsl.COMMITTEE_MEMBER_SCHEMA)
