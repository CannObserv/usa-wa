"""persons (#309): one row per live registry entity, survivorship applied.

Thin adapter over usa_wa_pipeline.conformed.entities.person_rows
(roster > WSL > PDC name precedence; logic + tests in the package), with
`name_own` from the curated OWN_NAMES table (#384, #501 D9).
"""

from usa_wa_pipeline.conformed.entities import PERSON_SCHEMA, person_rows
from usa_wa_pipeline.conformed.own_names import unmatched_own_names
from usa_wa_pipeline.frames import typed_relation


def model(dbt, session):
    dbt.config(materialized="table")
    crosswalk = dbt.ref("person_crosswalk").df().to_dict("records")
    rows = person_rows(
        crosswalk,
        sponsors=dbt.ref("stg_wsl_sponsors").df().to_dict("records"),
        roster=dbt.ref("stg_roster_members").df().to_dict("records"),
        pdc=dbt.ref("stg_pdc_winners").df().to_dict("records"),
    )
    # An entry that no longer names a live person publishing the form it
    # adjudicated is a stale adjudication (#501 D9): a reprinted edition, or a
    # key merged away. Refused here, with the corpus in hand; a hermetic build
    # has no persons to judge.
    if rows and (stale := unmatched_own_names(rows, crosswalk)):
        raise ValueError(
            "OWN_NAMES entries match no live person printing their form — "
            "re-adjudicate them in usa_wa_pipeline/conformed/own_names.py: "
            f"{[(e.roster_key, e.printed) for e in stale]}"
        )
    return typed_relation(session, rows, PERSON_SCHEMA)
