"""PowerMapClient wrapper tests — maps the generated SDK to the engine Protocol.

respx mocks the HTTP layer (the generated client's httpx transport). We verify
the wrapper's own logic: X-API-Key auth, feed→ChangePage mapping (cursor =
next_since), list offset/cursor arithmetic, observation→ObservationResult, and
404→None. Record-body mapping is a passthrough `to_dict()`, exercised end-to-end
by the engine's dict-based tests.
"""

import httpx
import pytest
import respx
from ulid import ULID

from clearinghouse_sync_powermap.client import RetryableClientError
from clearinghouse_sync_powermap.models import DISPOSITION_NEW, DISPOSITION_REJECTED
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient

BASE = "https://pm.test"


@pytest.fixture
async def client():
    c = GeneratedPowerMapClient(base_url=BASE, api_key="secret-key")
    yield c
    await c.aclose()


@respx.mock
async def test_get_changes_maps_feed_and_sends_auth(client):
    pm_id = ULID()
    route = respx.get(f"{BASE}/api/v1/changes").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "entity_type": "jurisdiction",
                        "entity_id": str(pm_id),
                        "changed_at": "2026-06-05T00:00:00Z",
                        "change_kind": "updated",
                        "archived_at": None,
                    }
                ],
                "meta": {
                    "limit": 100,
                    "count": 1,
                    "has_more": False,
                    "next_since": "2026-06-05T00:00:01Z",
                },
            },
        )
    )

    page = await client.get_changes(since=None)

    assert route.called
    assert route.calls.last.request.headers["X-API-Key"] == "secret-key"
    # since=None defaults to the epoch
    assert "since=1970" in str(route.calls.last.request.url)
    assert page.cursor == "2026-06-05T00:00:01Z"
    assert len(page.items) == 1
    item = page.items[0]
    assert item.entity_type == "jurisdiction"
    assert item.entity_id == pm_id
    assert item.change_kind == "updated"


@respx.mock
async def test_list_entities_advances_cursor_when_more(client):
    respx.get(f"{BASE}/api/v1/jurisdictions").mock(
        return_value=httpx.Response(
            200,
            json={"data": [], "meta": {"limit": 100, "offset": 0, "count": 0, "has_more": True}},
        )
    )

    page = await client.list_entities("/api/v1/jurisdictions")

    assert page.records == []
    assert page.cursor == "100"  # offset(0) + limit(100)


@respx.mock
async def test_list_entities_terminates_cursor_when_done(client):
    respx.get(f"{BASE}/api/v1/jurisdictions").mock(
        return_value=httpx.Response(
            200,
            json={"data": [], "meta": {"limit": 100, "offset": 100, "count": 0, "has_more": False}},
        )
    )

    page = await client.list_entities("/api/v1/jurisdictions", {"cursor": "100"})

    assert page.cursor is None


@respx.mock
async def test_post_observation_maps_disposition_and_anchor(client):
    pm_id = ULID()
    route = respx.post(f"{BASE}/api/v1/jurisdictions/observations").mock(
        return_value=httpx.Response(
            200,
            json={
                "disposition": DISPOSITION_NEW,
                "entity_id": str(pm_id),
                "entity_type": "jurisdiction",
            },
        )
    )

    result = await client.post_observation(
        "/api/v1/jurisdictions/observations",
        {
            "identifier_type": "ocd_division_id",
            "identifier_value": "ocd-division/country:us/state:wa/county:test",
            "slug": "usa-wa-county-test",
            "name": "Test County",
        },
    )

    assert route.called
    assert result.disposition == DISPOSITION_NEW
    assert result.pm_id == pm_id
    assert result.anchored


@respx.mock
async def test_get_entity_404_returns_none(client):
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(return_value=httpx.Response(404))

    assert await client.get_entity("/api/v1/jurisdictions", pm_id) is None


@respx.mock
async def test_get_entity_success_returns_dict(client):
    pm_id = ULID()
    type_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": str(pm_id),
                "slug": "usa-wa",
                "name": "Washington",
                "type": {"id": str(type_id), "slug": "state", "display_name": "State"},
                "recorded_at": "2026-06-05T00:00:00Z",
                "created_at": "2026-06-05T00:00:00Z",
                "updated_at": "2026-06-05T00:00:00Z",
            },
        )
    )

    record = await client.get_entity("/api/v1/jurisdictions", pm_id)

    assert record["slug"] == "usa-wa"
    assert record["name"] == "Washington"


@respx.mock
async def test_get_entity_non_404_status_reraises(client):
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(return_value=httpx.Response(403))

    with pytest.raises(Exception):  # noqa: B017 — generated UnexpectedStatus, not retryable
        await client.get_entity("/api/v1/jurisdictions", pm_id)


@respx.mock
async def test_post_observation_rejected(client):
    respx.post(f"{BASE}/api/v1/jurisdictions/observations").mock(
        return_value=httpx.Response(
            200, json={"disposition": DISPOSITION_REJECTED, "entity_id": None, "entity_type": None}
        )
    )

    result = await client.post_observation(
        "/api/v1/jurisdictions/observations",
        {
            "identifier_type": "ocd_division_id",
            "identifier_value": "ocd-division/country:us/state:wa",
            "slug": "usa-wa",
            "name": "Washington",
        },
    )

    assert result.rejected
    assert result.pm_id is None
    assert not result.anchored


@respx.mock
async def test_5xx_is_retryable(client):
    respx.get(f"{BASE}/api/v1/changes").mock(return_value=httpx.Response(503))

    with pytest.raises(RetryableClientError):
        await client.get_changes(since=None)


@respx.mock
async def test_422_raises_value_error(client):
    respx.post(f"{BASE}/api/v1/jurisdictions/observations").mock(
        return_value=httpx.Response(
            422, json={"detail": [{"loc": ["body", "name"], "msg": "field required", "type": "x"}]}
        )
    )

    with pytest.raises(ValueError, match="422"):
        await client.post_observation(
            "/api/v1/jurisdictions/observations",
            {"identifier_type": "t", "identifier_value": "v", "slug": "s", "name": "n"},
        )
