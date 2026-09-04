"""`/health/serving` (#313) — did this deployment load what was published?

The sibling of `/health/datasets`, and a different failure: a healthy catalog
with a stale serving load is the silent case, because every `/api/v1` answer is
still a 200. Currency is a VERSION comparison (CR 92) — an unchanged row count
is the normal case here, so counts cannot tell yesterday's snapshot from today's.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from usa_wa_api.serving.load import DATASETS_ROOT_ENV, create_serving_tables, ensure_serving_schema
from usa_wa_api.serving.schema import LoadState, Role

pytestmark = pytest.mark.db


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    (tmp_path / "catalog.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-04T00:00:00.000000Z",
                "datasets": [
                    {
                        "name": "roles",
                        "tier": "conformed",
                        "latest_version": "v2",
                        "rows": 1,
                        "generated_at": "2026-09-04T00:00:00.000000Z",
                    }
                ],
            }
        )
    )
    monkeypatch.setenv(DATASETS_ROOT_ENV, str(tmp_path))
    return tmp_path


async def _build(db_session):
    await ensure_serving_schema(db_session)
    await create_serving_tables(db_session)


async def test_an_unbuilt_schema_reports_not_loaded(client, db_session, catalog) -> None:
    """A fresh box before the first load. Asked directly of
    `information_schema`, not inferred from a failed query (CR 94)."""
    body = (await client.get("/health/serving")).json()
    assert body["loaded"] is False
    assert body["datasets"] == []


async def test_a_stale_load_is_caught_by_version_not_row_count(client, db_session, catalog) -> None:
    """CR 92: the case row counts are blind to. One role loaded from v1, the
    catalog now lists v2, and the counts agree exactly — which is the normal
    shape of a quiet day, and precisely why a count cannot answer this."""
    await _build(db_session)
    db_session.add(Role(role_key="seat:senate:ld-14", entity_id="01ROLE"))
    db_session.add(
        LoadState(
            dataset="roles",
            version="v1",
            sha256="sha256:abc",
            rows=1,
            loaded_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    body = (await client.get("/health/serving")).json()
    roles = next(d for d in body["datasets"] if d["name"] == "roles")
    assert roles["loaded_version"] == "v1"
    assert roles["published_version"] == "v2"
    assert roles["rows"] == 1  # counts agree; only the version does not
    assert roles["current"] is False


async def test_a_matching_version_is_current(client, db_session, catalog) -> None:
    await _build(db_session)
    db_session.add(
        LoadState(
            dataset="roles",
            version="v2",
            sha256="sha256:abc",
            rows=0,
            loaded_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    body = (await client.get("/health/serving")).json()
    roles = next(d for d in body["datasets"] if d["name"] == "roles")
    assert roles["current"] is True


async def test_a_dataset_the_catalog_does_not_carry_is_unknown_not_false(
    client, db_session, catalog
) -> None:
    """ "The catalog does not say" and "the catalog says otherwise" are different
    facts — the #180 posture this repo takes everywhere."""
    await _build(db_session)
    body = (await client.get("/health/serving")).json()
    assignments = next(d for d in body["datasets"] if d["name"] == "assignments")
    assert assignments["published_version"] is None
    assert assignments["current"] is None


async def test_rows_the_api_cannot_address_are_reported(client, db_session, catalog) -> None:
    """CR 95: a role published before the registrar bound its key carries a null
    entity_id, and NO gate can see it — `parity_spans` re-reads the registry, so
    it correctly reports zero. Only the loaded rows show what actually shipped.
    Reported, not gated: a new seat legitimately arrives this way."""
    await _build(db_session)
    db_session.add(Role(role_key="seat:senate:ld-14", entity_id="01ROLE"))
    db_session.add(Role(role_key="seat:house:ld-5:position-1", entity_id=None))
    await db_session.flush()

    body = (await client.get("/health/serving")).json()
    roles = next(d for d in body["datasets"] if d["name"] == "roles")
    assert roles["unaddressable_rows"] == 1


async def test_a_table_whose_entity_id_cannot_be_null_reports_no_such_count(
    client, db_session, catalog
) -> None:
    """`persons` is keyed on entity_id, so the count would be vacuously zero.
    Deriving the check from the column's nullability rather than a hand-kept
    list keeps it honest as the schema changes."""
    await _build(db_session)
    body = (await client.get("/health/serving")).json()
    persons = next(d for d in body["datasets"] if d["name"] == "persons")
    assert "unaddressable_rows" not in persons
