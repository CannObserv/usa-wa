"""org_crosswalk (#309): the registry's published identity surface.

Every org natural key with its entity ULID and the merge tombstone —
the ONLY re-point signal a consumer gets (spec § walkthrough). Reads the
registry via usa_wa_pipeline.registry_read (empty when no DATABASE_URL —
the hermetic gate).
"""

import pandas as pd

from usa_wa_pipeline.registry_read import CROSSWALK_COLUMNS, crosswalk_frame


def model(dbt, session):
    dbt.config(materialized="table")
    return pd.DataFrame(crosswalk_frame("org"), columns=CROSSWALK_COLUMNS)
