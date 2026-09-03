"""`/health/serving` (#313) — did this deployment load what was published?

The sibling of `/health/datasets`, and a different failure: a healthy catalog
with a stale serving load is the silent case, because every `/api/v1` answer is
still a 200.
"""

from __future__ import annotations

import json

import pytest

from usa_wa_api.serving.load import DATASETS_ROOT_ENV, create_serving_tables, ensure_serving_schema

pytestmark = pytest.mark.db


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-03T00:00:00.000000Z",
                "datasets": [
                    {
                        "name": "persons",
                        "tier": "conformed",
                        "latest_version": "v1",
                        "rows": 7,
                        "generated_at": "2026-09-03T00:00:00.000000Z",
                    }
                ],
            }
        )
    )
    monkeypatch.setenv(DATASETS_ROOT_ENV, str(tmp_path))
    return tmp_path


async def test_a_loaded_table_that_lags_the_catalog_is_not_current(
    client, db_session, catalog
) -> None:
    """The one comparison worth making. Nothing here 503s — the probe reports,
    the operator decides — but `current: false` names the dataset whose load
    did not take, which no 200 from `/api/v1` ever would."""
    await ensure_serving_schema(db_session)
    await create_serving_tables(db_session)

    body = (await client.get("/health/serving")).json()
    assert body["loaded"] is True
    persons = next(d for d in body["datasets"] if d["name"] == "persons")
    # the catalog says 7 published; nothing is loaded
    assert persons["published_rows"] == 7
    assert persons["rows"] == 0
    assert persons["current"] is False


async def test_a_dataset_the_catalog_does_not_carry_reports_no_published_rows(
    client, db_session, catalog
) -> None:
    """A served table with no catalog entry reports `published_rows: null`
    rather than 0 — "the catalog does not say" and "the catalog says none" are
    different facts, the #180 posture this repo takes everywhere."""
    await ensure_serving_schema(db_session)
    await create_serving_tables(db_session)

    body = (await client.get("/health/serving")).json()
    assignments = next(d for d in body["datasets"] if d["name"] == "assignments")
    assert assignments["published_rows"] is None
    assert assignments["current"] is False
