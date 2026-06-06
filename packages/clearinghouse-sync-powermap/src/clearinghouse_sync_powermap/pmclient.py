"""Concrete :class:`GeneratedPowerMapClient` over the generated ``powermap_client`` SDK.

Satisfies the :class:`clearinghouse_sync_powermap.client.PowerMapClient` Protocol.

This is the only module that imports the generated OpenAPI client. It adapts the
generated, typed operations to the engine's dict-based Protocol
(:mod:`clearinghouse_sync_powermap.client`): the engine speaks ``read_path`` /
``observe_path`` strings + plain dicts; this wrapper dispatches each path to the
right generated operation and converts models via ``to_dict`` / ``from_dict``.

Auth is PM's ``X-API-Key`` header, wired through the generated
``AuthenticatedClient`` (``prefix=""`` + ``auth_header_name``).
"""

from datetime import datetime
from typing import Any

from powermap_client import AuthenticatedClient
from powermap_client.api.public_api import (
    get_assignment,
    get_change_feed,
    get_jurisdiction,
    get_org,
    get_person,
    get_role,
    list_assignments,
    list_jurisdictions,
    list_roles,
    search_orgs,
    search_people,
    submit_assignment_observation,
    submit_jurisdiction_observation,
    submit_org_observation,
    submit_people_observation,
    submit_role_observation,
)
from powermap_client.errors import UnexpectedStatus
from powermap_client.models import (
    AssignmentObservationRequest,
    HTTPValidationError,
    JurisdictionObservationRequest,
    OrganizationObservationRequest,
    PeopleObservationRequest,
    RoleObservationRequest,
)

from clearinghouse_sync_powermap.client import (
    ChangeItem,
    ChangePage,
    EntityPage,
    ObservationResult,
    RetryableClientError,
)
from clearinghouse_sync_powermap.descriptors import as_ulid

#: ``since`` is required by the feed; first run (no cursor) starts at the epoch.
_EPOCH = "1970-01-01T00:00:00Z"


def _retryable(exc: UnexpectedStatus) -> bool:
    """Worth a backoff retry: rate-limit (429) or any server error (5xx)."""
    return exc.status_code == 429 or exc.status_code >= 500


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _list_paged(fn, *, with_query: bool):
    """Adapt a generated list/search op to a uniform ``(client, limit, offset)`` call."""

    async def call(client, limit: int, offset: int):
        kwargs = {"client": client, "limit": limit, "offset": offset}
        if with_query:
            kwargs["q"] = ""
        return await fn.asyncio_detailed(**kwargs)

    return call


class GeneratedPowerMapClient:
    """Engine-facing PM client backed by the generated SDK."""

    # read_path → paged list/search caller.
    # NOTE: people/orgs have no public list-all endpoint — only search, which
    # returns nothing for an empty query (verified against PM source). So
    # reconcile over people/orgs is a no-op; those entities are FEED-ONLY. The
    # step-6 descriptors must not depend on reconcile to enumerate them.
    _LIST = {
        "/api/v1/jurisdictions": _list_paged(list_jurisdictions, with_query=False),
        "/api/v1/roles": _list_paged(list_roles, with_query=False),
        "/api/v1/assignments": _list_paged(list_assignments, with_query=False),
        "/api/v1/people": _list_paged(search_people, with_query=True),
        "/api/v1/orgs": _list_paged(search_orgs, with_query=True),
    }
    # read_path → get-by-id op (first positional arg is the id)
    _GET = {
        "/api/v1/jurisdictions": get_jurisdiction,
        "/api/v1/roles": get_role,
        "/api/v1/assignments": get_assignment,
        "/api/v1/people": get_person,
        "/api/v1/orgs": get_org,
    }
    # observe_path → (submit op, request model)
    _OBSERVE = {
        "/api/v1/jurisdictions/observations": (
            submit_jurisdiction_observation,
            JurisdictionObservationRequest,
        ),
        "/api/v1/people/observations": (submit_people_observation, PeopleObservationRequest),
        "/api/v1/orgs/observations": (submit_org_observation, OrganizationObservationRequest),
        "/api/v1/roles/observations": (submit_role_observation, RoleObservationRequest),
        "/api/v1/assignments/observations": (
            submit_assignment_observation,
            AssignmentObservationRequest,
        ),
    }

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self._client = AuthenticatedClient(
            base_url=base_url.rstrip("/"),
            token=api_key,
            prefix="",
            auth_header_name="X-API-Key",
            timeout=timeout,
            raise_on_unexpected_status=True,
        )

    async def _send(self, awaitable):
        """Await a generated op; map 5xx/429 to a retryable error and a 422
        ``HTTPValidationError`` body to a clear ``ValueError``."""
        try:
            resp = await awaitable
        except UnexpectedStatus as exc:
            if _retryable(exc):
                raise RetryableClientError(f"PM {exc.status_code}") from exc
            raise
        parsed = resp.parsed
        if isinstance(parsed, HTTPValidationError):
            raise ValueError(f"PM rejected the request (422): {parsed.to_dict()}")
        return parsed

    async def get_changes(self, since: str | None, limit: int = 100) -> ChangePage:
        # The generated op types ``since`` as a datetime (calls ``.isoformat()``),
        # so parse the stored cursor string; first run starts at the epoch.
        since_dt = datetime.fromisoformat((since or _EPOCH).replace("Z", "+00:00"))
        feed = await self._send(
            get_change_feed.asyncio_detailed(client=self._client, since=since_dt, limit=limit)
        )
        items = [
            ChangeItem(
                # PM entity ids are ULIDs by cohort convention; a non-ULID id would
                # raise here and fail the page (acceptable — it signals a contract break).
                entity_type=ci.entity_type.value,
                entity_id=as_ulid(ci.entity_id),
                changed_at=_parse_ts(ci.changed_at),
                change_kind=ci.change_kind.value,
            )
            for ci in feed.data
        ]
        return ChangePage(items=items, cursor=feed.meta.next_since)

    async def list_entities(self, read_path: str, params: dict | None = None) -> EntityPage:
        caller = self._LIST[read_path]
        offset = int((params or {}).get("cursor") or 0)
        limit = int((params or {}).get("limit") or 100)
        body = await self._send(caller(self._client, limit, offset))
        records = [item.to_dict() for item in body.data]
        next_cursor = str(offset + limit) if body.meta.has_more else None
        return EntityPage(records=records, cursor=next_cursor)

    async def get_entity(self, read_path: str, pm_id: Any) -> dict | None:
        try:
            resp = await self._GET[read_path].asyncio_detailed(str(pm_id), client=self._client)
        except UnexpectedStatus as exc:
            if exc.status_code == 404:
                return None  # entity gone (e.g. deleted between feed and fetch)
            if _retryable(exc):
                raise RetryableClientError(f"PM {exc.status_code}") from exc
            raise
        parsed = resp.parsed
        if parsed is None or isinstance(parsed, HTTPValidationError):
            return None
        return parsed.to_dict()

    async def post_observation(self, observe_path: str, payload: dict) -> ObservationResult:
        submit_fn, model_cls = self._OBSERVE[observe_path]
        body = await self._send(
            submit_fn.asyncio_detailed(client=self._client, body=model_cls.from_dict(payload))
        )
        entity_id = getattr(body, "entity_id", None)
        pm_id = as_ulid(entity_id) if isinstance(entity_id, str) else None
        return ObservationResult(disposition=body.disposition, pm_id=pm_id, raw=body.to_dict())

    async def aclose(self) -> None:
        await self._client.get_async_httpx_client().aclose()
