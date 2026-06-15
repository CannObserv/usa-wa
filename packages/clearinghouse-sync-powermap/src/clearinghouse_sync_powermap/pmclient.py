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
from typing import Any, NoReturn

from powermap_client import AuthenticatedClient
from powermap_client.api.public_api import (
    delete_subscriptions_bulk,
    discover_subscriptions,
    get_assignment,
    get_change_feed,
    get_jurisdiction,
    get_org,
    get_person,
    get_role,
    list_assignments,
    list_jurisdictions,
    list_roles,
    list_subscriptions,
    register_subscriptions,
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
    DiscoverSubscriptionsRootType,
    HTTPValidationError,
    JurisdictionObservationRequest,
    ListSubscriptionsEntityTypeType0,
    OrganizationObservationRequest,
    PeopleObservationRequest,
    RoleObservationRequest,
    SubscriptionBulkDeleteRequest,
    SubscriptionRegisterRequest,
)

from clearinghouse_sync_powermap.client import (
    ChangeItem,
    ChangePage,
    DeliveryBlockedError,
    DiscoveredEntity,
    EntityPage,
    ObservationResult,
    PayloadRejectedError,
    RetryableClientError,
    SubscriptionResult,
)
from clearinghouse_sync_powermap.descriptors import as_ulid

#: Permanent auth/permission statuses: the credential is wrong, not the payload.
#: Mapped to :class:`DeliveryBlockedError` so the engine parks to UNAVAILABLE.
_BLOCKED_STATUSES = frozenset({401, 403})


def _retryable(exc: UnexpectedStatus) -> bool:
    """Worth a backoff retry: rate-limit (429) or any server error (5xx)."""
    return exc.status_code == 429 or exc.status_code >= 500


def _raise_mapped(exc: UnexpectedStatus) -> NoReturn:
    """Translate a non-retryable SDK ``UnexpectedStatus`` into the engine's portable
    permanent-failure vocabulary. Caller has already ruled out retryable statuses.

    Always raises (hence ``NoReturn``): callers fall through to use ``resp`` only on
    the no-exception path, so a future non-raising edit here would be a type error.

    - 401/403 → :class:`DeliveryBlockedError` (auth/scope; park to UNAVAILABLE).
    - any other 4xx → :class:`PayloadRejectedError` (payload refused; park to REJECTED).
    """
    if exc.status_code in _BLOCKED_STATUSES:
        raise DeliveryBlockedError(f"PM {exc.status_code}") from exc
    raise PayloadRejectedError(f"PM {exc.status_code}") from exc


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
    # search_path → (search op, supports_jurisdiction). Powers the match cascade.
    _SEARCH = {
        "/api/v1/people/search": (search_people, False),
        "/api/v1/orgs/search": (search_orgs, True),
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
        """Await a generated op; map 5xx/429 to a retryable error, permanent auth
        statuses to :class:`DeliveryBlockedError`, and a non-retryable 4xx (incl. a
        422 ``HTTPValidationError`` body) to :class:`PayloadRejectedError` — so no
        raw SDK exception escapes to crash-loop the sync cycle."""
        try:
            resp = await awaitable
        except UnexpectedStatus as exc:
            if _retryable(exc):
                raise RetryableClientError(f"PM {exc.status_code}") from exc
            _raise_mapped(exc)
        parsed = resp.parsed
        if isinstance(parsed, HTTPValidationError):
            raise PayloadRejectedError(f"PM rejected the request (422): {parsed.to_dict()}")
        return parsed

    async def get_changes(self, after: int | None, limit: int = 100) -> ChangePage:
        # PM #203: the cursor is an outbox seq_id (``after``, ``>`` exclusive), not a
        # timestamp. None → 0 ("from the start"); the feed is subscription-filtered.
        feed = await self._send(
            get_change_feed.asyncio_detailed(client=self._client, after=after or 0, limit=limit)
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
        return ChangePage(items=items, next_after=feed.meta.next_after)

    async def discover(
        self,
        *,
        root_type: str,
        root_id: str,
        follow,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DiscoveredEntity]:
        # Paginate the graph traversal internally (PM ``limit``/``offset``); ``follow``
        # is sent as a single comma-separated string. Returns the flattened candidates.
        results: list[DiscoveredEntity] = []
        follow_param = ",".join(follow)
        root = DiscoverSubscriptionsRootType(root_type)
        while True:
            body = await self._send(
                discover_subscriptions.asyncio_detailed(
                    client=self._client,
                    root_type=root,
                    root_id=root_id,
                    follow=follow_param,
                    limit=limit,
                    offset=offset,
                )
            )
            results.extend(
                DiscoveredEntity(
                    entity_type=di.entity_type.value,
                    entity_id=as_ulid(di.entity_id),
                    display_name=di.display_name if isinstance(di.display_name, str) else None,
                    hops_from_root=di.hops_from_root,
                )
                for di in body.data
            )
            if not body.meta.has_more:
                return results
            offset += limit

    async def list_subscriptions(self, *, entity_type: str | None = None) -> list:
        # Paginate the subscription list; collect just the entity ids (engine diffs ids).
        ids: list = []
        offset = 0
        limit = 100
        type_param = (
            ListSubscriptionsEntityTypeType0(entity_type) if entity_type is not None else None
        )
        while True:
            kwargs: dict[str, Any] = {"client": self._client, "limit": limit, "offset": offset}
            if type_param is not None:
                kwargs["entity_type"] = type_param
            body = await self._send(list_subscriptions.asyncio_detailed(**kwargs))
            ids.extend(as_ulid(item.entity_id) for item in body.data)
            if not body.meta.has_more:
                return ids
            offset += limit

    async def add_subscriptions(self, entity_ids) -> SubscriptionResult:
        body = await self._send(
            register_subscriptions.asyncio_detailed(
                client=self._client,
                body=SubscriptionRegisterRequest(entity_ids=[str(i) for i in entity_ids]),
            )
        )
        return SubscriptionResult(
            registered=body.registered,
            already_subscribed=body.already_subscribed,
            not_found=[as_ulid(x) for x in body.not_found],
        )

    async def remove_subscriptions(self, entity_ids) -> int:
        # Bulk DELETE returns 204 (no count); report the requested count on success.
        # Unused today (pruning deferred), wired for surface completeness.
        ids = [str(i) for i in entity_ids]
        await self._send(
            delete_subscriptions_bulk.asyncio_detailed(
                client=self._client, body=SubscriptionBulkDeleteRequest(entity_ids=ids)
            )
        )
        return len(ids)

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
            _raise_mapped(exc)
        parsed = resp.parsed
        if parsed is None or isinstance(parsed, HTTPValidationError):
            return None
        return parsed.to_dict()

    async def search_entities(
        self,
        search_path: str,
        *,
        q: str | None = None,
        identifier_type: str | None = None,
        identifier_value: str | None = None,
        jurisdiction: str | None = None,
        limit: int = 20,
    ) -> EntityPage:
        op, supports_jur = self._SEARCH[search_path]
        # The search ops require ``q``; empty string + an identifier/jurisdiction
        # filter narrows by that filter (verified against the live API). NOTE: ``q``
        # filters by name server-side via FTS (``@@ plainto_tsquery``) since
        # power-map#201 — word-token matching that folds ``&``/punctuation/word-order
        # (and accents for people); #199 was the earlier ILIKE precursor. The match
        # cascade issues a single such query and confirms client-side.
        kwargs: dict[str, Any] = {"client": self._client, "q": q or "", "limit": limit}
        # Identifier match is on the type+value PAIR; one without the other is a
        # no-op filter, so only apply it when both are present.
        if identifier_type is not None and identifier_value is not None:
            kwargs["identifier_type"] = identifier_type
            kwargs["identifier_value"] = identifier_value
        # The jurisdiction filter applies to orgs only (people carry no jurisdiction).
        if supports_jur and jurisdiction is not None:
            kwargs["jurisdiction"] = jurisdiction
        body = await self._send(op.asyncio_detailed(**kwargs))
        if body is None:  # defensive: unexpected null body on 200 → empty page
            return EntityPage(records=[], cursor=None)
        records = [item.to_dict() for item in body.data]
        return EntityPage(records=records, cursor=None)

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
