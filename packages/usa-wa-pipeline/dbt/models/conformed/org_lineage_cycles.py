"""org_lineage_cycles (#447): lineage cycles that cannot be read in time order.

Thin adapter over usa_wa_pipeline.conformed.lineage.untimely_cycles — why a
time-ordered cycle is legitimate (WSL re-uses committee ids, #126) lives there.

NOT published — `publish.PUBLISHED_DATASETS` is an explicit list — but
materialized all the same: gated at zero by tests/org_lineage_time_ordered.sql,
the table is empty on a clean build and the hand-review work order otherwise.
"""

from usa_wa_pipeline.conformed.lineage import CYCLE_SCHEMA, untimely_cycles
from usa_wa_pipeline.frames import typed_relation


def model(dbt, session):
    dbt.config(materialized="table")
    rows = untimely_cycles(dbt.ref("org_lineage").df().to_dict("records"))
    return typed_relation(session, rows, CYCLE_SCHEMA)
