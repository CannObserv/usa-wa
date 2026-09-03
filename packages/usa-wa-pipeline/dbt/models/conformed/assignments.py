"""assignments (#309): merged tenure spans, joined to the person crosswalk.

Thin binder over the pure, pytest-covered `usa_wa_pipeline.conformed.spans`
(docs/PIPELINE.md § TDD policy). The span engine and every guard it carries
are imported unchanged from the domain and the adapter; the 4-part span
`source_id` becomes real columns here.
"""

import os
from datetime import UTC, datetime

import pandas as pd

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_pipeline.conformed.spans import (
    ASSIGNMENT_COLUMNS,
    SpanInputs,
    assignment_rows,
    build_all_spans,
    entity_index,
)
from usa_wa_pipeline.operator_read import operator_events
from usa_wa_pipeline.registry_read import crosswalk_frame

logger = get_logger(__name__)


def model(dbt, session):
    dbt.config(materialized="table")
    # the repo's convention (USA_WA_BIENNIUM overrides for a scoped rebuild),
    # matching what every Phase-B builder uses to decide which spans stay open
    current_biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(
        datetime.now(UTC).date()
    )
    spans = build_all_spans(
        SpanInputs(
            sponsors=dbt.ref("stg_wsl_sponsors").df().to_dict("records"),
            committee_members=dbt.ref("stg_wsl_committee_members").df().to_dict("records"),
            roster=dbt.ref("stg_roster_members").df().to_dict("records"),
            events=operator_events(),
        ),
        current_biennium=current_biennium,
    )
    rows, counters = assignment_rows(spans, entity_index(crosswalk_frame("person")))
    # `unregistered_spans` is the ONLY signal that a registrar gap dropped spans
    # on the join (CR 60): the table just comes back smaller, and a build that
    # discards the count publishes that silently.
    log = logger.warning if counters["unregistered_spans"] else logger.info
    log("assignments_built", extra={"summary": counters})
    return pd.DataFrame(rows, columns=ASSIGNMENT_COLUMNS)
