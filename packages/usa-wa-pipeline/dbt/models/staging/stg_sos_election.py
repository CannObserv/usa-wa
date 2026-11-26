"""stg_sos_election: thin dbt adapter over usa_wa_pipeline.staging.sos.election_rows (#600).

Every distinct platform election JSON at its first fetch: the first with
``is_official_results`` true dates certification. Logic + tests live in the Python package.
"""

from clearinghouse_core.rawstore import RawStore, get_raw_root
from usa_wa_adapter_sos.coverage import SOS_RESULTS_SOURCE_SLUG
from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.staging import sos


def model(dbt, session):
    dbt.config(materialized="table")
    rows = sos.election_rows(RawStore(get_raw_root(), SOS_RESULTS_SOURCE_SLUG))
    return typed_relation(session, rows, sos.ELECTION_SCHEMA)
