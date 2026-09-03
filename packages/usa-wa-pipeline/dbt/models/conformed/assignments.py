"""assignments (#309): merged tenure spans, joined to the person crosswalk.

Thin binder over the pure, pytest-covered `usa_wa_pipeline.conformed.spans`
(docs/PIPELINE.md § TDD policy). The span engine and every guard it carries
are imported unchanged from the domain and the adapter; the 4-part span
`source_id` becomes real columns here.
"""

import os
from datetime import UTC, datetime

import pandas as pd

from clearinghouse_core.registry import KIND_PERSON
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_pipeline.conformed.spans import (
    ASSIGNMENT_COLUMNS,
    ROSTER_SOURCE,
    SOURCE,
    SpanInputs,
    assignment_rows,
    build_all_spans,
    build_roster_spans,
    entity_index,
    roster_resolution,
)
from usa_wa_pipeline.operator_read import operator_events
from usa_wa_pipeline.registry_read import crosswalk_frame


def model(dbt, session):
    dbt.config(materialized="table")
    # the repo's convention (USA_WA_BIENNIUM overrides for a scoped rebuild),
    # matching what every Phase-B builder uses to decide which spans stay open
    current_biennium = os.environ.get("USA_WA_BIENNIUM") or biennium_for_date(
        datetime.now(UTC).date()
    )
    sponsors = dbt.ref("stg_wsl_sponsors").df().to_dict("records")
    roster = dbt.ref("stg_roster_members").df().to_dict("records")
    events = operator_events()

    # ONE resolve of the ~8,600-record roster corpus feeds both families: the
    # WSL-joined half deepens the sponsor build (#228), the minted half IS the
    # roster family. Resolving twice would double the cost and let the halves
    # disagree about who is WSL-joined.
    resolution = roster_resolution(roster, sponsors)
    spans = build_all_spans(
        SpanInputs(
            sponsors=sponsors,
            committee_members=dbt.ref("stg_wsl_committee_members").df().to_dict("records"),
            roster=roster,
            sos_results=dbt.ref("stg_sos_results").df().to_dict("records"),
            events=events,
        ),
        current_biennium=current_biennium,
        extra_observations=resolution.joined,
    )
    roster_spans = build_roster_spans(
        resolution,
        events=events,
        current_biennium=current_biennium,
        # #267: the only other-kind spans a minted identity could hold. None do
        # today (the WSL family keys on numeric ids), but the seam is wired.
        context_spans=spans,
    )
    # The join's counters — `unregistered_spans` above all — are reported by
    # `usa_wa_pipeline.parity_spans`, not from here (CR 68). A `dbt build`
    # never calls `configure_logging`, so a logger in a Python model emits
    # nothing: the info path is dropped and the warning path reaches
    # `logging.lastResort`, which prints the message and discards `extra`.
    # Round 4 logged from here and the counters reached no one; the probe
    # recomputes the same join under the job harness, where they are real.
    rows, _counters = assignment_rows(
        {SOURCE: spans, ROSTER_SOURCE: roster_spans},
        entity_index(crosswalk_frame(KIND_PERSON)),
    )
    return pd.DataFrame(rows, columns=ASSIGNMENT_COLUMNS)
