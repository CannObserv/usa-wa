"""citations (#313): every published entity → the raw wires that attest it.

Thin binder over the pure, pytest-covered `usa_wa_pipeline.conformed.citations`
(docs/PIPELINE.md § TDD policy). Internal tier: materialized and loaded into the
serving schema so `/provenance/{type}/{id}` keeps answering after the Postgres
provenance tables retire, but not part of the subscriber contract.

Counters are reported by `usa_wa_pipeline.parity_spans`, not from here — a
`dbt build` never calls `configure_logging`, so a logger in a Python model
emits nothing (the round-4 lesson recorded in `conformed/assignments.py`).
"""

import pandas as pd

from usa_wa_pipeline.conformed.citations import CITATION_COLUMNS, CitationInputs, citation_rows


def model(dbt, session):
    dbt.config(materialized="table")
    rows, _counters = citation_rows(
        CitationInputs(
            person_crosswalk=dbt.ref("person_crosswalk").df().to_dict("records"),
            org_crosswalk=dbt.ref("org_crosswalk").df().to_dict("records"),
            roles=dbt.ref("roles").df().to_dict("records"),
            assignments=dbt.ref("assignments").df().to_dict("records"),
            sponsors=dbt.ref("stg_wsl_sponsors").df().to_dict("records"),
            committee_members=dbt.ref("stg_wsl_committee_members").df().to_dict("records"),
            committees=dbt.ref("stg_wsl_committees").df().to_dict("records"),
            meetings=dbt.ref("stg_wsl_meetings").df().to_dict("records"),
            pdc=dbt.ref("stg_pdc_winners").df().to_dict("records"),
            roster=dbt.ref("stg_roster_members").df().to_dict("records"),
        )
    )
    return pd.DataFrame(rows, columns=CITATION_COLUMNS)
