"""person_crosswalk (#309): the registry's published identity surface.

Every person natural key with its entity ULID and the merge tombstone —
the ONLY re-point signal a consumer gets (spec § walkthrough). Reads the
registry via usa_wa_pipeline.registry_read (empty only under the explicit
USA_WA_PIPELINE_HERMETIC gate; a missing DATABASE_URL fails the build).
"""

import pandas as pd

from usa_wa_pipeline.registry_read import CROSSWALK_COLUMNS, crosswalk_frame


def model(dbt, session):
    dbt.config(materialized="table")
    frame = pd.DataFrame(crosswalk_frame("person"), columns=CROSSWALK_COLUMNS)
    # pin VARCHAR from day one: an all-None merged_into otherwise infers
    # INTEGER and the column type flips on the first real tombstone (#302 CR)
    frame["merged_into"] = frame["merged_into"].astype("string")
    return frame
