"""person_name_collisions (#378): two live entities publishing one person's name.

Thin adapter over usa_wa_pipeline.conformed.namesakes.collision_rows — the fold,
the allowlist and the reasoning live in the package beside their tests, as
`persons` does over `entities.person_rows`.

A python model rather than a SQL test, because the grouping key is
`identity_fold`: Python, in usa-wa-adapter-legislature, and `persons` publishes
only (entity_id, name_full, name_source), so there is no fold in SQL's reach.
Re-deriving it in SQL is the second implementation CR 155 consolidated away.

NOT published — `publish.PUBLISHED_DATASETS` is an explicit list — but
materialized all the same: gated at zero, the table is empty on a clean build
and is the hand-review work order on any other.
"""

import pandas as pd

from usa_wa_pipeline.conformed.namesakes import COLLISION_COLUMNS, collision_rows


def model(dbt, session):
    dbt.config(materialized="table")
    rows = collision_rows(dbt.ref("persons").df().to_dict("records"))
    return pd.DataFrame(rows, columns=COLLISION_COLUMNS)
