"""stg_roster_members: thin dbt adapter over usa_wa_pipeline.staging.roster (#306).

Parses the newest archived roster-PDF revision through the adapter's real
extraction; logic + tests live in the Python package.
"""

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.staging import roster


def model(dbt, session):
    dbt.config(materialized="table")
    rows = roster.roster_rows(RawStore(get_raw_root(), "usa_wa_legislature_roster"))
    return typed_relation(session, rows, roster.ROSTER_SCHEMA)
