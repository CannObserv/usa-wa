"""PowerMapClient wrapper tests — maps the generated SDK to the engine Protocol.

respx mocks the HTTP layer (the generated client's httpx transport). We verify
the wrapper's own logic: X-API-Key auth, feed→ChangePage mapping (integer
``next_after`` cursor), discovery/subscription pagination, list offset/cursor
arithmetic, observation→ObservationResult, and 404→None. Record-body mapping is a
passthrough `to_dict()`, exercised end-to-end by the engine's dict-based tests.
"""

import httpx
import pytest
import respx
from ulid import ULID

from clearinghouse_sync_powermap.client import (
    DeliveryBlockedError,
    PayloadRejectedError,
    RetryableClientError,
)
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
                        "seq_id": 6,
                        "entity_type": "jurisdiction",
                        "entity_id": str(pm_id),
                        "changed_at": "2026-06-05T00:00:00Z",
                        "change_kind": "updated",
                    }
                ],
                "meta": {
                    "limit": 100,
                    "count": 1,
                    "has_more": False,
                    "next_after": 6,
                },
            },
        )
    )

    page = await client.get_changes(after=None)

    assert route.called
    assert route.calls.last.request.headers["X-API-Key"] == "secret-key"
    # after=None defaults to seq 0 ("from the start")
    assert "after=0" in str(route.calls.last.request.url)
    assert page.next_after == 6
    assert len(page.items) == 1
    item = page.items[0]
    assert item.entity_type == "jurisdiction"
    assert item.entity_id == pm_id
    assert item.change_kind == "updated"
    assert item.merged_into is None  # absent field → None (not the Unset sentinel)


@respx.mock
async def test_get_changes_maps_merged_into_on_delete(client):
    """A merge `deleted` event carries merged_into (power-map#235) — map it to a ULID;
    leave it None when the field is absent (a genuine delete)."""
    loser, winner = ULID(), ULID()
    respx.get(f"{BASE}/api/v1/changes").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "seq_id": 7,
                        "entity_type": "organization",
                        "entity_id": str(loser),
                        "changed_at": "2026-06-05T00:00:00Z",
                        "change_kind": "deleted",
                        "merged_into": str(winner),
                    },
                    {
                        "seq_id": 8,
                        "entity_type": "organization",
                        "entity_id": str(ULID()),
                        "changed_at": "2026-06-05T00:00:00Z",
                        "change_kind": "deleted",
                    },
                ],
                "meta": {"limit": 100, "count": 2, "has_more": False, "next_after": 8},
            },
        )
    )

    page = await client.get_changes(after=None)

    assert page.items[0].merged_into == winner  # merge → winner id
    assert page.items[1].merged_into is None  # genuine delete → None


@respx.mock
async def test_list_role_types_maps_catalog(client):
    """The role_types catalog (power-map#268) comes back as raw dicts."""
    respx.get(f"{BASE}/api/v1/role-types").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "01REP",
                        "slug": "state_representative",
                        "display_name": "State Representative",
                        "expects_jurisdiction": True,
                        "requires_qualifier": True,
                    },
                    {
                        "id": "01OWN",
                        "slug": "owner",
                        "display_name": "Owner",
                        "expects_jurisdiction": False,
                        "requires_qualifier": False,
                    },
                ]
            },
        )
    )

    rows = await client.list_role_types()

    assert [r["slug"] for r in rows] == ["state_representative", "owner"]
    assert rows[0]["expects_jurisdiction"] is True and rows[1]["expects_jurisdiction"] is False
    # power-map#273 enforced flag flows through the catalog read (first-class after regen).
    assert rows[0]["requires_qualifier"] is True and rows[1]["requires_qualifier"] is False


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
async def test_search_entities_passes_identifier_and_jurisdiction(client):
    """The match cascade's search: identifier + jurisdiction params reach orgs/search,
    and summary records come back as dicts."""
    route = respx.get(f"{BASE}/api/v1/orgs/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "01ABC", "name": "WA House Approps Committee", "parent_id": "01XYZ"}
                ],
                "meta": {"limit": 20, "offset": 0, "count": 1, "has_more": False},
            },
        )
    )

    page = await client.search_entities(
        "/api/v1/orgs/search",
        q="approps",
        identifier_type="org_wa_legislature_committee_id",
        identifier_value="C-1",
        jurisdiction="usa-wa",
    )

    assert route.called
    url = str(route.calls.last.request.url)
    assert "identifier_type=org_wa_legislature_committee_id" in url
    assert "identifier_value=C-1" in url
    assert "jurisdiction=usa-wa" in url
    assert page.records[0]["name"] == "WA House Approps Committee"


@respx.mock
async def test_search_entities_ignores_lone_identifier_type(client):
    """Identifier match needs the type+value pair — a lone type is not applied."""
    route = respx.get(f"{BASE}/api/v1/orgs/search").mock(
        return_value=httpx.Response(
            200,
            json={"data": [], "meta": {"limit": 20, "offset": 0, "count": 0, "has_more": False}},
        )
    )

    await client.search_entities(
        "/api/v1/orgs/search", identifier_type="org_wa_legislature_chamber"
    )

    assert "identifier_type" not in str(route.calls.last.request.url)


@respx.mock
async def test_search_entities_paginates_up_to_cap(client):
    """Search paginates by PM offset, accumulating up to ``limit`` records across
    pages (carrying ``has_more`` the way list_entities does), so a correct candidate
    on a later page is no longer silently dropped at the first 20."""
    pages = [
        httpx.Response(
            200,
            json={
                "data": [{"id": "01A", "name": "A"}],
                "meta": {"limit": 1, "offset": 0, "count": 1, "has_more": True},
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [{"id": "01B", "name": "B"}],
                "meta": {"limit": 1, "offset": 1, "count": 1, "has_more": False},
            },
        ),
    ]
    route = respx.get(f"{BASE}/api/v1/orgs/search").mock(side_effect=pages)

    page = await client.search_entities("/api/v1/orgs/search", q="x", limit=10)

    assert route.call_count == 2
    assert [r["id"] for r in page.records] == ["01A", "01B"]
    assert page.cursor is None  # callers do not page; the wrapper gathers internally


@respx.mock
async def test_search_entities_offset_advances_by_returned_count(client):
    """Offset advances by the rows PM actually returned, not the requested page size.
    A short non-final page (PM caps its page below the requested limit) must resume at
    the next un-seen record — never skip the gap between len(records) and the cap."""
    pages = [
        httpx.Response(
            200,
            json={
                "data": [{"id": "01A", "name": "A"}, {"id": "01B", "name": "B"}],
                "meta": {"limit": 10, "offset": 0, "count": 2, "has_more": True},
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [{"id": "01C", "name": "C"}],
                "meta": {"limit": 10, "offset": 2, "count": 1, "has_more": False},
            },
        ),
    ]
    route = respx.get(f"{BASE}/api/v1/orgs/search").mock(side_effect=pages)

    page = await client.search_entities("/api/v1/orgs/search", q="x", limit=10)

    assert [r["id"] for r in page.records] == ["01A", "01B", "01C"]
    # The 2nd request must resume at offset=2 (the rows already taken), not offset=10
    # (the requested page size) — the latter would skip records 2..9.
    assert "offset=2" in str(route.calls[1].request.url)


@respx.mock
async def test_search_entities_warns_when_truncated_at_cap(client, caplog):
    """When PM still reports ``has_more`` after the cap is filled, the truncated
    candidate set is surfaced as a warning rather than silently dropped."""
    respx.get(f"{BASE}/api/v1/orgs/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"id": "01A", "name": "A"}, {"id": "01B", "name": "B"}],
                "meta": {"limit": 2, "offset": 0, "count": 2, "has_more": True},
            },
        )
    )

    with caplog.at_level("WARNING"):
        page = await client.search_entities("/api/v1/orgs/search", q="x", limit=2)

    assert len(page.records) == 2  # capped at the requested limit
    assert any(r.msg == "search_match_truncated" for r in caplog.records)


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
async def test_get_entity_403_raises_blocked(client):
    """A non-retryable auth/scope status (403) maps to the portable
    DeliveryBlockedError, not a raw SDK UnexpectedStatus — so the engine can park
    it without importing the generated client's exceptions."""
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(return_value=httpx.Response(403))

    with pytest.raises(DeliveryBlockedError):
        await client.get_entity("/api/v1/jurisdictions", pm_id)


@respx.mock
async def test_get_entity_5xx_is_retryable(client):
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(return_value=httpx.Response(503))

    with pytest.raises(RetryableClientError):
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
        await client.get_changes(after=None)


def _disc_item(pm_id, entity_type, hops, name="X"):
    return {
        "entity_type": entity_type,
        "entity_id": str(pm_id),
        "hops_from_root": hops,
        "display_name": name,
    }


@respx.mock
async def test_discover_paginates_and_maps(client):
    """Discovery follows the ``has_more`` flag across pages and flattens the result,
    sending root_type/root_id and the comma-joined follow set."""
    a, b = ULID(), ULID()
    pages = [
        httpx.Response(
            200,
            json={
                "data": [_disc_item(a, "jurisdiction", 0)],
                "meta": {"limit": 1, "offset": 0, "count": 1, "has_more": True},
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [_disc_item(b, "organization", 2)],
                "meta": {"limit": 1, "offset": 1, "count": 1, "has_more": False},
            },
        ),
    ]
    route = respx.get(f"{BASE}/api/v1/subscriptions/discover").mock(side_effect=pages)

    found = await client.discover(
        root_type="jurisdiction", root_id="usa-wa", follow=["lineage", "roles"], limit=1
    )

    assert route.call_count == 2
    first_url = str(route.calls[0].request.url)
    assert "root_type=jurisdiction" in first_url
    assert "root_id=usa-wa" in first_url
    assert "follow=lineage%2Croles" in first_url  # comma-joined, URL-encoded
    assert [(d.entity_type, d.entity_id, d.hops_from_root) for d in found] == [
        ("jurisdiction", a, 0),
        ("organization", b, 2),
    ]


@respx.mock
async def test_discover_warns_when_truncated(client, caplog):
    """A truncated traversal (hard cap hit) is surfaced, not silently under-returned."""
    respx.get(f"{BASE}/api/v1/subscriptions/discover").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [_disc_item(ULID(), "jurisdiction", 0)],
                "meta": {
                    "limit": 1,
                    "offset": 0,
                    "count": 1,
                    "has_more": False,
                    "truncated": True,
                },
            },
        )
    )

    with caplog.at_level("WARNING"):
        found = await client.discover(
            root_type="jurisdiction", root_id="usa-wa", follow=["lineage"]
        )

    assert len(found) == 1
    assert any(r.msg == "discovery_truncated" for r in caplog.records)


@respx.mock
async def test_discover_bounds_runaway_pagination(client, caplog):
    """A misbehaving PM that always returns ``has_more=true`` (or never advances the
    offset) must not spin the daemon forever: the safety bound trips, logs a warning,
    and returns the partial result instead of looping unbounded."""
    from clearinghouse_sync_powermap.pmclient import _MAX_PAGINATION_PAGES

    route = respx.get(f"{BASE}/api/v1/subscriptions/discover").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [_disc_item(ULID(), "jurisdiction", 0)],
                "meta": {"limit": 1, "offset": 0, "count": 1, "has_more": True},
            },
        )
    )

    with caplog.at_level("WARNING"):
        found = await client.discover(
            root_type="jurisdiction", root_id="usa-wa", follow=["lineage"], limit=1
        )

    # Bounded: at most _MAX_PAGINATION_PAGES requests, then break with the partial set.
    assert route.call_count == _MAX_PAGINATION_PAGES
    assert len(found) == _MAX_PAGINATION_PAGES
    assert any(r.msg == "discover_pagination_bound_exceeded" for r in caplog.records)


@respx.mock
async def test_list_subscriptions_bounds_runaway_pagination(client, caplog):
    """``list_subscriptions`` mirrors ``discover``: a never-terminating ``has_more``
    feed trips the same safety bound + warning rather than spinning forever."""
    from clearinghouse_sync_powermap.pmclient import _MAX_PAGINATION_PAGES

    route = respx.get(f"{BASE}/api/v1/subscriptions").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "entity_id": str(ULID()),
                        "entity_type": "jurisdiction",
                        "created_at": "2026-06-05T00:00:00Z",
                    }
                ],
                "meta": {"limit": 1, "offset": 0, "count": 1, "has_more": True},
            },
        )
    )

    with caplog.at_level("WARNING"):
        ids = await client.list_subscriptions()

    assert route.call_count == _MAX_PAGINATION_PAGES
    assert len(ids) == _MAX_PAGINATION_PAGES
    assert any(r.msg == "list_subscriptions_pagination_bound_exceeded" for r in caplog.records)


@respx.mock
async def test_list_subscriptions_paginates_to_ids(client):
    a, b = ULID(), ULID()

    def _sub(pm_id):
        return {
            "entity_id": str(pm_id),
            "entity_type": "jurisdiction",
            "created_at": "2026-06-05T00:00:00Z",
        }

    pages = [
        httpx.Response(
            200,
            json={
                "data": [_sub(a)],
                "meta": {"limit": 1, "offset": 0, "count": 1, "has_more": True},
            },
        ),
        httpx.Response(
            200,
            json={
                "data": [_sub(b)],
                "meta": {"limit": 1, "offset": 1, "count": 1, "has_more": False},
            },
        ),
    ]
    respx.get(f"{BASE}/api/v1/subscriptions").mock(side_effect=pages)

    ids = await client.list_subscriptions()

    assert ids == [a, b]


@respx.mock
async def test_add_subscriptions_maps_result(client):
    nf = ULID()
    route = respx.post(f"{BASE}/api/v1/subscriptions").mock(
        return_value=httpx.Response(
            200, json={"registered": 2, "already_subscribed": 1, "not_found": [str(nf)]}
        )
    )

    result = await client.add_subscriptions([ULID(), ULID(), ULID()])

    assert route.called
    assert result.registered == 2
    assert result.already_subscribed == 1
    assert result.not_found == [nf]


@respx.mock
async def test_add_subscriptions_chunks_over_batch_limit(client):
    """PM caps POST /subscriptions at 500 ids; add_subscriptions chunks larger sets
    and aggregates the per-batch results."""
    import json

    ids = [ULID() for _ in range(1001)]
    seen_sizes = []

    def _handler(request):
        n = len(json.loads(request.content)["entity_ids"])
        seen_sizes.append(n)
        return httpx.Response(200, json={"registered": n, "already_subscribed": 0, "not_found": []})

    respx.post(f"{BASE}/api/v1/subscriptions").mock(side_effect=_handler)

    result = await client.add_subscriptions(ids)

    assert seen_sizes == [500, 500, 1]  # chunked at the 500-item cap
    assert result.registered == 1001  # aggregated across batches


@respx.mock
async def test_remove_subscriptions_reports_requested_count(client):
    """Bulk DELETE returns 204 (no body); the wrapper reports the requested count."""
    respx.delete(f"{BASE}/api/v1/subscriptions").mock(return_value=httpx.Response(204))

    removed = await client.remove_subscriptions([ULID(), ULID(), ULID()])

    assert removed == 3


@respx.mock
async def test_remove_subscriptions_chunks_over_batch_limit(client):
    """PM caps DELETE /subscriptions at 500 ids (a prune can target thousands, #73);
    remove_subscriptions chunks larger sets and sums the per-batch counts — else PM
    422s the whole call ('List should have at most 500 items')."""
    import json

    ids = [ULID() for _ in range(1001)]
    seen_sizes = []

    def _handler(request):
        seen_sizes.append(len(json.loads(request.content)["entity_ids"]))
        return httpx.Response(204)

    respx.delete(f"{BASE}/api/v1/subscriptions").mock(side_effect=_handler)

    removed = await client.remove_subscriptions(ids)

    assert seen_sizes == [500, 500, 1]  # chunked at the 500-item cap
    assert removed == 1001  # aggregated across batches


@respx.mock
async def test_add_subscriptions_403_raises_blocked(client):
    """Missing ``subscriptions:write`` scope → DeliveryBlockedError (operator grants
    the scope), surfaced through the same mapping as the write path."""
    respx.post(f"{BASE}/api/v1/subscriptions").mock(
        return_value=httpx.Response(403, json={"detail": "Insufficient scope"})
    )

    with pytest.raises(DeliveryBlockedError):
        await client.add_subscriptions([ULID()])


@respx.mock
async def test_422_raises_payload_rejected(client):
    """A 422 schema rejection maps to PayloadRejectedError — a permanent payload
    refusal the engine parks to REJECTED rather than crash-looping the cycle."""
    respx.post(f"{BASE}/api/v1/jurisdictions/observations").mock(
        return_value=httpx.Response(
            422, json={"detail": [{"loc": ["body", "name"], "msg": "field required", "type": "x"}]}
        )
    )

    with pytest.raises(PayloadRejectedError, match="422"):
        await client.post_observation(
            "/api/v1/jurisdictions/observations",
            {"identifier_type": "t", "identifier_value": "v", "slug": "s", "name": "n"},
        )


@respx.mock
async def test_post_observation_403_raises_blocked(client):
    """403 insufficient-scope on a write → DeliveryBlockedError (operator fixes the
    key, then redrives), not a raw UnexpectedStatus that would escape the engine."""
    respx.post(f"{BASE}/api/v1/jurisdictions/observations").mock(
        return_value=httpx.Response(403, json={"detail": "Insufficient scope"})
    )

    with pytest.raises(DeliveryBlockedError):
        await client.post_observation(
            "/api/v1/jurisdictions/observations",
            {"identifier_type": "t", "identifier_value": "v", "slug": "s", "name": "n"},
        )


@respx.mock
async def test_post_observation_400_raises_payload_rejected(client):
    """A non-auth permanent 4xx (e.g. 400) is treated as a payload refusal → REJECTED."""
    respx.post(f"{BASE}/api/v1/jurisdictions/observations").mock(
        return_value=httpx.Response(400, json={"detail": "bad request"})
    )

    with pytest.raises(PayloadRejectedError):
        await client.post_observation(
            "/api/v1/jurisdictions/observations",
            {"identifier_type": "t", "identifier_value": "v", "slug": "s", "name": "n"},
        )


def _event_json(event_id: str, *, slug: str = "birth", year: int | None = 1970) -> dict:
    """A minimal PM read EntityEvent body (EntityEvent.from_dict-shaped)."""
    return {
        "id": event_id,
        "event_type": {"id": str(ULID()), "slug": slug, "display_name": slug.title()},
        "date": {"year": year} if year is not None else {},
        "visibility": "public",
        "created_at": "2026-05-01T00:00:00Z",
    }


@respx.mock
async def test_list_entity_events_people_paginates_and_flattens(client):
    """The people sub-resource is fetched by parent id, paginated via meta, flattened."""
    pm_id = ULID()
    route = respx.get(f"{BASE}/api/v1/people/{pm_id}/events").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [_event_json("evt-1")],
                    "meta": {"limit": 100, "offset": 0, "count": 1, "has_more": True},
                },
            ),
            httpx.Response(
                200,
                json={
                    "data": [_event_json("evt-2", slug="death", year=2024)],
                    "meta": {"limit": 100, "offset": 1, "count": 1, "has_more": False},
                },
            ),
        ]
    )

    events = await client.list_entity_events("/api/v1/people", pm_id)

    assert [e["id"] for e in events] == ["evt-1", "evt-2"]
    assert events[0]["event_type"]["slug"] == "birth"
    assert events[1]["date"]["year"] == 2024
    assert route.call_count == 2
    assert route.calls[0].request.headers["X-API-Key"] == "secret-key"


@respx.mock
async def test_list_entity_events_orgs_single_page(client):
    """The orgs sub-resource dispatches to the /orgs/{id}/events route."""
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/orgs/{pm_id}/events").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [_event_json("evt-o", slug="founding")],
                "meta": {"limit": 100, "offset": 0, "count": 1, "has_more": False},
            },
        )
    )

    events = await client.list_entity_events("/api/v1/orgs", pm_id)

    assert [e["id"] for e in events] == ["evt-o"]


@respx.mock
async def test_list_entity_events_404_returns_empty(client):
    """A parent gone between the feed and the events fetch → empty list, not a crash."""
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/people/{pm_id}/events").mock(
        return_value=httpx.Response(404, json={"detail": "not found"})
    )

    assert await client.list_entity_events("/api/v1/people", pm_id) == []


# --- #85: 429 Retry-After surfacing + central read pacing -------------------------


@respx.mock
async def test_429_retryable_carries_retry_after(client):
    """A PM 429 (rate limit, live since the #84 companion hardening) surfaces
    ``Retry-After`` on the retryable error so read loops can pause-and-resume."""
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "7"})
    )

    with pytest.raises(RetryableClientError, match="429") as excinfo:
        await client.get_entity("/api/v1/jurisdictions", pm_id)

    assert excinfo.value.retry_after == 7.0


@respx.mock
async def test_429_without_retry_after_header(client):
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(return_value=httpx.Response(429))

    with pytest.raises(RetryableClientError) as excinfo:
        await client.get_entity("/api/v1/jurisdictions", pm_id)

    assert excinfo.value.retry_after is None


@respx.mock
async def test_429_on_send_path_carries_retry_after(client):
    """The shared ``_send`` mapping (feed/list/observe ops) carries Retry-After too."""
    respx.get(f"{BASE}/api/v1/changes").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "3"})
    )

    with pytest.raises(RetryableClientError) as excinfo:
        await client.get_changes(after=None)

    assert excinfo.value.retry_after == 3.0


@respx.mock
async def test_429_non_numeric_retry_after_is_none(client):
    """An HTTP-date Retry-After (rare) degrades to None, not a crash."""
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "Wed, 15 Jul 2026 07:28:00 GMT"})
    )

    with pytest.raises(RetryableClientError) as excinfo:
        await client.get_entity("/api/v1/jurisdictions", pm_id)

    assert excinfo.value.retry_after is None


@respx.mock
async def test_min_request_interval_spaces_calls():
    """#85 (the #77 pattern for PM): with a min interval configured, back-to-back
    calls through the client are spaced — the central governor no single caller
    (the person backstop's ~300-GET crawl) can burst past."""
    import time

    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(return_value=httpx.Response(404))
    paced = GeneratedPowerMapClient(base_url=BASE, api_key="k", min_request_interval=0.05)
    try:
        start = time.monotonic()
        await paced.get_entity("/api/v1/jurisdictions", pm_id)
        await paced.get_entity("/api/v1/jurisdictions", pm_id)
        await paced.get_entity("/api/v1/jurisdictions", pm_id)
        elapsed = time.monotonic() - start
    finally:
        await paced.aclose()

    assert elapsed >= 0.1  # two enforced gaps of >= 0.05s each


@respx.mock
async def test_min_request_interval_default_off(client):
    """The default client is unpaced — pacing is the deployment's knob
    (SidecarSettings.powermap_min_request_interval), not a library tax.

    Asserted on the gate's configured interval and its reservation state rather than on
    wall-clock elapsed time (CR-2). A `< 50ms for three calls` budget is a load-sensitive
    tripwire: it flaked at 59ms when an unrelated test left an event listener on a pooled
    connection, and the failure pointed at the rate limiter rather than the real cause.
    An unpaced gate is one that never reserves a slot, which is directly observable."""
    pm_id = ULID()
    respx.get(f"{BASE}/api/v1/jurisdictions/{pm_id}").mock(return_value=httpx.Response(404))

    assert client._gate._min == 0.0  # configured off — the library default

    for _ in range(3):
        await client.get_entity("/api/v1/jurisdictions", pm_id)

    # A disabled gate returns before reserving, so its slot cursor never advances.
    assert client._gate._next_at == 0.0
