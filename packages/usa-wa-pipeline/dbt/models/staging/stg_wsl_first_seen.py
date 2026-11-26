"""stg_wsl_first_seen: thin dbt adapter over usa_wa_pipeline.staging.wsl.first_seen_rows (#600).

Each member's first listing per biennium, across every archived sponsors wire: the
member-elect's first-evidence date. Logic + tests live in the Python package.
"""

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG
from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.staging import wsl


def model(dbt, session):
    dbt.config(materialized="table")
    rows = wsl.first_seen_rows(RawStore(get_raw_root(), WSL_SOURCE_SLUG))
    return typed_relation(session, rows, wsl.FIRST_SEEN_SCHEMA)
