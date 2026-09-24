"""Every dbt Python model builds with its declared column types (#361).

The hermetic build reads empty sources, so every model in it is empty — which is
exactly the case duckdb's type inference gets wrong (an ``object`` column with
no values reads ``INTEGER``). If the empty build carries the declared types,
the types are the model's, not the rows'. A model that gains a column without
declaring it, or returns a bare frame again, goes red here rather than
resurfacing as a dbt test that cannot bind.
"""

import duckdb
import pytest

import usa_wa_pipeline
from usa_wa_pipeline.conformed.citations import CITATION_SCHEMA
from usa_wa_pipeline.conformed.entities import ORG_SCHEMA, PERSON_SCHEMA
from usa_wa_pipeline.conformed.namesakes import COLLISION_SCHEMA
from usa_wa_pipeline.conformed.roles import ROLE_SCHEMA
from usa_wa_pipeline.conformed.spans import ASSIGNMENT_SCHEMA
from usa_wa_pipeline.matching.roster_wsl import LINK_SCHEMA
from usa_wa_pipeline.registry_read import CROSSWALK_SCHEMA
from usa_wa_pipeline.staging.fetches import FETCH_SCHEMA
from usa_wa_pipeline.staging.pdc import WINNER_SCHEMA
from usa_wa_pipeline.staging.roster import ROSTER_SCHEMA
from usa_wa_pipeline.staging.sos import FILING_SCHEMA, RESULT_SCHEMA
from usa_wa_pipeline.staging.wsl import (
    COMMITTEE_MEMBER_SCHEMA,
    COMMITTEE_SCHEMA,
    MEETING_SCHEMA,
    SPONSOR_SCHEMA,
)

MODEL_SCHEMAS = {
    "stg_pdc_winners": WINNER_SCHEMA,
    "stg_raw_fetches": FETCH_SCHEMA,
    "stg_roster_members": ROSTER_SCHEMA,
    "stg_sos_filings": FILING_SCHEMA,
    "stg_sos_results": RESULT_SCHEMA,
    "stg_wsl_committee_members": COMMITTEE_MEMBER_SCHEMA,
    "stg_wsl_committees": COMMITTEE_SCHEMA,
    "stg_wsl_meetings": MEETING_SCHEMA,
    "stg_wsl_sponsors": SPONSOR_SCHEMA,
    "match_roster_wsl": LINK_SCHEMA,
    "assignments": ASSIGNMENT_SCHEMA,
    "citations": CITATION_SCHEMA,
    "org_crosswalk": CROSSWALK_SCHEMA,
    "organizations": ORG_SCHEMA,
    "person_crosswalk": CROSSWALK_SCHEMA,
    "person_name_collisions": COLLISION_SCHEMA,
    "persons": PERSON_SCHEMA,
    "roles": ROLE_SCHEMA,
}


def test_every_python_model_declares_a_schema() -> None:
    """The drift guard: a new Python model is covered, or this says which is not."""
    models = {path.stem for path in (usa_wa_pipeline.PROJECT_DIR / "models").rglob("*.py")}
    assert models == set(MODEL_SCHEMAS)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.parametrize("model", sorted(MODEL_SCHEMAS))
def test_the_empty_build_carries_the_declared_types(hermetic_build, model) -> None:
    con = duckdb.connect(str(hermetic_build))
    try:
        built = [(name, type_) for name, type_, *_ in con.execute(f'describe "{model}"').fetchall()]
    finally:
        con.close()
    assert built == list(MODEL_SCHEMAS[model].items())
