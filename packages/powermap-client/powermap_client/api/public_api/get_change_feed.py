from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.change_feed_response import ChangeFeedResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    after: int,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["after"] = after

    params["limit"] = limit

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/changes",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | ChangeFeedResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ChangeFeedResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = cast(Any, None)
        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | ChangeFeedResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    after: int,
    limit: int | Unset = 50,
) -> Response[Any | ChangeFeedResponse | HTTPValidationError]:
    """Get Changes

     Return subscribed entity changes with id > after.

    Only events for entities this API key has explicitly subscribed to are returned.
    A key with no subscriptions receives an empty feed.

    Pass ``meta.next_after`` from the previous response as ``after`` on each
    subsequent poll. The cursor is exclusive (``>``), so no deduplication is needed.

    Args:
        after (int): Outbox seq_id cursor (exclusive). Pass 0 for all events.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ChangeFeedResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        after=after,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    after: int,
    limit: int | Unset = 50,
) -> Any | ChangeFeedResponse | HTTPValidationError | None:
    """Get Changes

     Return subscribed entity changes with id > after.

    Only events for entities this API key has explicitly subscribed to are returned.
    A key with no subscriptions receives an empty feed.

    Pass ``meta.next_after`` from the previous response as ``after`` on each
    subsequent poll. The cursor is exclusive (``>``), so no deduplication is needed.

    Args:
        after (int): Outbox seq_id cursor (exclusive). Pass 0 for all events.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ChangeFeedResponse | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        after=after,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    after: int,
    limit: int | Unset = 50,
) -> Response[Any | ChangeFeedResponse | HTTPValidationError]:
    """Get Changes

     Return subscribed entity changes with id > after.

    Only events for entities this API key has explicitly subscribed to are returned.
    A key with no subscriptions receives an empty feed.

    Pass ``meta.next_after`` from the previous response as ``after`` on each
    subsequent poll. The cursor is exclusive (``>``), so no deduplication is needed.

    Args:
        after (int): Outbox seq_id cursor (exclusive). Pass 0 for all events.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | ChangeFeedResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        after=after,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    after: int,
    limit: int | Unset = 50,
) -> Any | ChangeFeedResponse | HTTPValidationError | None:
    """Get Changes

     Return subscribed entity changes with id > after.

    Only events for entities this API key has explicitly subscribed to are returned.
    A key with no subscriptions receives an empty feed.

    Pass ``meta.next_after`` from the previous response as ``after`` on each
    subsequent poll. The cursor is exclusive (``>``), so no deduplication is needed.

    Args:
        after (int): Outbox seq_id cursor (exclusive). Pass 0 for all events.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | ChangeFeedResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            after=after,
            limit=limit,
        )
    ).parsed
