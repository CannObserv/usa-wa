"""assignments (#309): merged tenure spans, joined to the person crosswalk.

Thin binder over the pure, pytest-covered `usa_wa_pipeline.conformed.spans`
(docs/PIPELINE.md § TDD policy). The span engine and every guard it carries
are imported unchanged from the domain and the adapter; the 4-part span
`source_id` becomes real columns here.
"""

from clearinghouse_core.registry import KIND_PERSON
from usa_wa_pipeline.conformed.spans import (
    ASSIGNMENT_SCHEMA,
    SpanInputs,
    assignment_rows,
    build_families,
    current_biennium,
    entity_index,
)
from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.operator_read import operator_events
from usa_wa_pipeline.registry_read import crosswalk_frame


def model(dbt, session):
    dbt.config(materialized="table")
    # One resolve of the roster corpus feeds both families — see
    # `conformed.spans.build_families`, the sequence `parity_spans` and
    # `registry_coverage` run too.
    families = build_families(
        SpanInputs(
            sponsors=dbt.ref("stg_wsl_sponsors").df().to_dict("records"),
            committee_members=dbt.ref("stg_wsl_committee_members").df().to_dict("records"),
            roster=dbt.ref("stg_roster_members").df().to_dict("records"),
            sos_results=dbt.ref("stg_sos_results").df().to_dict("records"),
            events=operator_events(),
        ),
        # USA_WA_BIENNIUM pins a scoped rebuild; shared with both probes
        current_biennium=current_biennium(),
    )
    # The join's counters — `unregistered_spans` above all — are reported by
    # `usa_wa_pipeline.registry_coverage`, not from here (CR 68; #412 PR B). A
    # `dbt build` never calls `configure_logging`, so a logger in a Python model
    # emits nothing: the info path is dropped and the warning path reaches
    # `logging.lastResort`, which prints the message and discards `extra`.
    # Round 4 logged from here and the counters reached no one; the probe
    # recomputes the same join under the job harness, after the registrar.
    rows, _counters = assignment_rows(families, entity_index(crosswalk_frame(KIND_PERSON)))
    return typed_relation(session, rows, ASSIGNMENT_SCHEMA)
