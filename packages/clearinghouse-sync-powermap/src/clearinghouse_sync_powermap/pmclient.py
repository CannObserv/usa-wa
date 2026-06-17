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

from collections.abc import Sequence
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
    list_org_events,
    list_person_events,
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
from ulid import ULID

from clearinghouse_core.logging import get_logger
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

logger = get_logger(__name__)

#: Permanent auth/permission statuses: the credential is wrong, not the payload.
#: Mapped to :class:`DeliveryBlockedError` so the engine parks to UNAVAILABLE.
_BLOCKED_STATUSES = frozenset({401, 403})

#: PM caps ``POST /api/v1/subscriptions`` at 500 entity ids per request (422 above
#: that). add_subscriptions chunks larger sets to stay under the cap.
_SUBSCRIBE_BATCH = 500

#: Safety ceiling on the live ``discover`` / ``list_subscriptions`` pagination loops
#: (PM #203). Both run every sidecar cycle via the discovery backstop; a misbehaving
#: PM that always returns ``has_more=true`` (or a non-advancing offset) would otherwise
#: spin the daemon forever. At the loops' default ``limit`` (100/page) this is ~100k
#: records — orders of magnitude above the WA identity cohort — so it never trips in
#: normal operation; it is a runaway guard, not a tuning knob. On exceed: warn + break
#: with the partial set (mirrors the ``discovery_truncated`` surfacing style).
_MAX_PAGINATION_PAGES = 1000


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
    # parent read_path → per-parent events list op (the /{id}/events sub-resource).
    _EVENTS = {
        "/api/v1/people": list_person_events,
        "/api/v1/orgs": list_org_events,
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
        follow: Sequence[str],
        limit: int = 100,
        offset: int = 0,
    ) -> list[DiscoveredEntity]:
        # Paginate the graph traversal internally (PM ``limit``/``offset``); ``follow``
        # is sent as a single comma-separated string. Returns the flattened candidates.
        results: list[DiscoveredEntity] = []
        follow_param = ",".join(follow)
        root = DiscoverSubscriptionsRootType(root_type)
        for _page in range(_MAX_PAGINATION_PAGES):
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
            if getattr(body.meta, "truncated", False):
                # PM hit a hard traversal cap: the subtree is larger than this response
                # window and the set is silently incomplete. Surface it rather than
                # under-subscribing without a trace.
                logger.warning(
                    "discovery_truncated",
                    extra={"root_type": root_type, "root_id": root_id, "offset": offset},
                )
            if not body.meta.has_more:
                return results
            offset += limit
        # Safety bound hit: PM kept signalling ``has_more`` past the page ceiling
        # (misbehaving feed or non-advancing offset). Stop the live loop and surface
        # the truncated result rather than spinning the daemon forever.
        logger.warning(
            "discover_pagination_bound_exceeded",
            extra={
                "root_type": root_type,
                "root_id": root_id,
                "max_pages": _MAX_PAGINATION_PAGES,
                "collected": len(results),
            },
        )
        return results

    async def list_subscriptions(self, *, entity_type: str | None = None) -> list[ULID]:
        # Paginate the subscription list; collect just the entity ids (engine diffs ids).
        ids: list[ULID] = []
        offset = 0
        limit = 100
        type_param = (
            ListSubscriptionsEntityTypeType0(entity_type) if entity_type is not None else None
        )
        for _page in range(_MAX_PAGINATION_PAGES):
            kwargs: dict[str, Any] = {"client": self._client, "limit": limit, "offset": offset}
            if type_param is not None:
                kwargs["entity_type"] = type_param
            body = await self._send(list_subscriptions.asyncio_detailed(**kwargs))
            ids.extend(as_ulid(item.entity_id) for item in body.data)
            if not body.meta.has_more:
                return ids
            offset += limit
        # Safety bound hit (see _MAX_PAGINATION_PAGES): a never-terminating ``has_more``
        # would otherwise spin the daemon forever. Stop and return the partial id set.
        logger.warning(
            "list_subscriptions_pagination_bound_exceeded",
            extra={
                "entity_type": entity_type,
                "max_pages": _MAX_PAGINATION_PAGES,
                "collected": len(ids),
            },
        )
        return ids

    async def add_subscriptions(self, entity_ids: Sequence[ULID]) -> SubscriptionResult:
        # Chunk at PM's 500-id cap (discovery can return thousands); aggregate the
        # per-batch counts so the caller sees one combined result.
        ids = [str(i) for i in entity_ids]
        registered = 0
        already_subscribed = 0
        not_found: list[ULID] = []
        for start in range(0, len(ids), _SUBSCRIBE_BATCH):
            chunk = ids[start : start + _SUBSCRIBE_BATCH]
            body = await self._send(
                register_subscriptions.asyncio_detailed(
                    client=self._client,
                    body=SubscriptionRegisterRequest(entity_ids=chunk),
                )
            )
            registered += body.registered
            already_subscribed += body.already_subscribed
            not_found.extend(as_ulid(x) for x in body.not_found)
        return SubscriptionResult(
            registered=registered,
            already_subscribed=already_subscribed,
            not_found=not_found,
        )

    async def remove_subscriptions(self, entity_ids: Sequence[ULID]) -> int:
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

    async def list_entity_events(self, read_path: str, pm_id: Any) -> list[dict]:
        """Page the per-parent ``/{id}/events`` sub-resource into raw event dicts.

        Dispatches by the parent ``read_path`` (people/orgs) and follows
        ``meta.has_more`` the way :meth:`list_entities` does. A 404 (parent gone
        between the feed and this fetch) yields an empty list rather than crashing
        the cycle — symmetric with :meth:`get_entity`."""
        op = self._EVENTS[read_path]
        records: list[dict] = []
        offset = 0
        limit = 100
        for _page in range(_MAX_PAGINATION_PAGES):
            try:
                resp = await op.asyncio_detailed(
                    str(pm_id), client=self._client, limit=limit, offset=offset
                )
            except UnexpectedStatus as exc:
                if exc.status_code == 404:
                    return []  # parent gone between feed and fetch
                if _retryable(exc):
                    raise RetryableClientError(f"PM {exc.status_code}") from exc
                _raise_mapped(exc)
            body = resp.parsed
            if body is None or isinstance(body, HTTPValidationError):
                return records
            records.extend(item.to_dict() for item in body.data)
            if not getattr(body.meta, "has_more", False):
                return records
            offset += len(body.data)
        # Safety bound hit (see _MAX_PAGINATION_PAGES): a never-terminating
        # ``has_more`` (e.g. an empty page that never advances ``offset``) would
        # otherwise spin forever. Stop and surface the partial set, like
        # :meth:`list_subscriptions` — silent truncation reads as a short event list.
        logger.warning(
            "list_entity_events_pagination_bound_exceeded",
            extra={"read_path": read_path, "pm_id": str(pm_id), "max_pages": _MAX_PAGINATION_PAGES},
        )
        return records

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
        # (and accents for people); #199 was the earlier ILIKE precursor.
        #
        # ``limit`` is treated as a MAX-RECORD cap on the candidate set the match
        # cascade confirms client-side: the wrapper paginates by PM ``offset`` and
        # accumulates up to ``limit`` records (carrying ``meta.has_more`` the way
        # list_entities does), so a correct candidate beyond PM's first page is no
        # longer silently dropped. The cap stays caller-controlled (#12) — the
        # cascade narrows by jurisdiction + hierarchy, so a small cap is normal —
        # and a still-truncated set (``has_more`` true at the cap) is logged rather
        # than dropped without a trace.
        records: list[dict] = []
        offset = 0
        for _page in range(_MAX_PAGINATION_PAGES):
            page_limit = limit - len(records)
            if page_limit <= 0:
                break
            kwargs: dict[str, Any] = {"client": self._client, "q": q or "", "limit": page_limit}
            # Identifier match is on the type+value PAIR; one without the other is a
            # no-op filter, so only apply it when both are present.
            if identifier_type is not None and identifier_value is not None:
                kwargs["identifier_type"] = identifier_type
                kwargs["identifier_value"] = identifier_value
            # The jurisdiction filter applies to orgs only (people carry no jurisdiction).
            if supports_jur and jurisdiction is not None:
                kwargs["jurisdiction"] = jurisdiction
            if offset:
                kwargs["offset"] = offset
            body = await self._send(op.asyncio_detailed(**kwargs))
            if body is None:  # defensive: unexpected null body on 200 → empty page
                break
            records.extend(item.to_dict() for item in body.data)
            if not getattr(body.meta, "has_more", False):
                break
            if len(records) >= limit:
                # Cap filled but PM has more: the confirmed candidate set is
                # truncated. Surface it (a correct candidate past the cap reads as
                # "new" → a mergeable duplicate) rather than dropping it silently.
                logger.warning(
                    "search_match_truncated",
                    extra={"search_path": search_path, "q": q, "cap": limit},
                )
                break
            # Advance by the rows PM actually returned, not the requested page size:
            # if PM caps its page below ``page_limit`` (a short non-final page), an
            # ``offset += page_limit`` would skip the records in between.
            offset += len(body.data)
        return EntityPage(records=records[:limit], cursor=None)

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
