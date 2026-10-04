"""org_lineage (#447): the operator-attested committee succession links, published.

Thin adapter over usa_wa_pipeline.conformed.lineage.lineage_rows. Reads the
current links from the registry via usa_wa_pipeline.operator_read (empty only
under the explicit USA_WA_PIPELINE_HERMETIC gate; a missing DATABASE_URL fails
the build) and resolves each WSL id through org_crosswalk.
"""

from usa_wa_pipeline.conformed.lineage import LINEAGE_SCHEMA, lineage_rows
from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.operator_read import succession_links


def model(dbt, session):
    dbt.config(materialized="table")
    crosswalk = dbt.ref("org_crosswalk").df().to_dict("records")
    return typed_relation(session, lineage_rows(crosswalk, succession_links()), LINEAGE_SCHEMA)
