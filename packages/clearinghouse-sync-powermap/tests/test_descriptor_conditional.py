"""Descriptor conditional-fetch seam (usa-wa#160): fetch_record_conditional short-circuits
on 304 and shares _attach_children with the feed's fetch_record so both paths agree on
what a full record contains."""

from typing import Any

from ulid import ULID

from clearinghouse_sync_powermap.testing import FakeClient, FakeDescriptor


class _ChildDescriptor(FakeDescriptor):
    """A descriptor that attaches a child sub-resource, like person/org attach events."""

    async def _attach_children(self, client: Any, pm_id: Any, record: dict) -> dict:
        record["children"] = ["c1"]
        return record


def _rec(pm_id):
    return {"id": str(pm_id), "source": "wsl", "source_id": "1", "name": "N"}


async def test_fetch_record_conditional_304_short_circuits():
    pm_id = ULID()
    client = FakeClient(entities={pm_id: _rec(pm_id)}, not_modified_ids={pm_id})
    fetch = await FakeDescriptor().fetch_record_conditional(client, pm_id, if_none_match='"e1"')
    assert fetch.not_modified is True and fetch.record is None
    # 304 → no children attached, and no plain get_entity fallback
    assert client.fetched == []


async def test_fetch_record_conditional_200_attaches_children_and_returns_etag():
    pm_id = ULID()
    client = FakeClient(entities={pm_id: _rec(pm_id)}, entity_etags={pm_id: '"e2"'})
    fetch = await _ChildDescriptor().fetch_record_conditional(
        client, pm_id, if_none_match='"stale"'
    )
    assert fetch.not_modified is False and fetch.etag == '"e2"'
    assert fetch.record["children"] == ["c1"]  # _attach_children ran on the 200


async def test_fetch_record_conditional_404_routes_to_heal():
    pm_id = ULID()
    client = FakeClient(entities={})  # absent → record None
    fetch = await FakeDescriptor().fetch_record_conditional(client, pm_id, if_none_match='"e"')
    assert fetch.record is None and fetch.not_modified is False


async def test_feed_fetch_record_still_attaches_children_after_refactor():
    """Regression: the _attach_children extraction must keep fetch_record (feed path)
    attaching children exactly as before."""
    pm_id = ULID()
    # Separate clients: FakeClient.get_entity returns the same dict object each call
    # (production returns a fresh to_dict()), so a shared dict would alias across the two.
    record = await _ChildDescriptor().fetch_record(FakeClient(entities={pm_id: _rec(pm_id)}), pm_id)
    assert record["children"] == ["c1"]

    # base descriptor (no children) is unchanged: record passes through
    base = await FakeDescriptor().fetch_record(FakeClient(entities={pm_id: _rec(pm_id)}), pm_id)
    assert "children" not in base
