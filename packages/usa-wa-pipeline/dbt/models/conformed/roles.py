"""roles (#309): the seat/slot dimension the assignments point at.

Thin binder over the pure, pytest-covered `usa_wa_pipeline.conformed.roles`
(docs/PIPELINE.md § TDD policy). Every key function is imported unchanged from
the adapter and the WA vocabulary — a role key is structural, so it needs no
registry; only the organization it belongs to is registry-joined.
"""

import pandas as pd

from clearinghouse_core.registry import KIND_ORG, KIND_ROLE
from usa_wa_pipeline.conformed.roles import ROLE_COLUMNS, role_rows
from usa_wa_pipeline.conformed.spans import entity_index
from usa_wa_pipeline.registry_read import crosswalk_frame


def model(dbt, session):
    dbt.config(materialized="table")
    # Two crosswalks: the role's own ULID (#313) and the org it sits in. The
    # role one is keyed on `role_key`, which the seed carried across from
    # `canonical.roles` so PM's 312 anchors stay valid.
    rows, _counters = role_rows(
        dbt.ref("assignments").df().to_dict("records"),
        entity_index(crosswalk_frame(KIND_ORG)),
        entity_index(crosswalk_frame(KIND_ROLE)),
    )
    frame = pd.DataFrame(rows, columns=ROLE_COLUMNS)
    # `district` is null for party/committee roles, and a plain int64 column
    # cannot hold that — pandas widens to float64 and the published CSV reads
    # "10.0" for LD 10. The nullable integer dtype keeps it an LD number.
    frame["district"] = frame["district"].astype("Int64")
    return frame
