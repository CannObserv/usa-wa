"""person_crosswalk (#309): the registry's published identity surface.

Every person natural key with its entity ULID and the merge tombstone —
the ONLY re-point signal a consumer gets (spec § walkthrough). Reads the
registry via usa_wa_pipeline.registry_read (empty only under the explicit
USA_WA_PIPELINE_HERMETIC gate; a missing DATABASE_URL fails the build).
"""

from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.registry_read import CROSSWALK_SCHEMA, crosswalk_frame


def model(dbt, session):
    dbt.config(materialized="table")
    # the declared schema pins merged_into VARCHAR from day one: all-None, it
    # would otherwise infer INTEGER and flip on the first real tombstone (#302 CR)
    return typed_relation(session, crosswalk_frame("person"), CROSSWALK_SCHEMA)
