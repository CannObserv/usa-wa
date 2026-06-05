import datetime
from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.change_feed_response import ChangeFeedResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    since: datetime.datetime,
    limit: int | Unset = 50,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_since = since.isoformat()
    params["since"] = json_since

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
) -> ChangeFeedResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = ChangeFeedResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[ChangeFeedResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    since: datetime.datetime,
    limit: int | Unset = 50,
) -> Response[ChangeFeedResponse | HTTPValidationError]:
    """Get Changes

     Return entities updated, archived, or deleted since the given timestamp.

    Clients should pass ``meta.next_since`` from the previous response as
    ``since`` on each subsequent poll.  The ``since`` comparison is inclusive
    (>=) to avoid dropping events at exact timestamp boundaries; de-duplicate
    the overlap row using ``entity_id`` if needed.

    Args:
        since (datetime.datetime): ISO 8601 timestamp; changes at or after this.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChangeFeedResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        since=since,
        limit=limit,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    since: datetime.datetime,
    limit: int | Unset = 50,
) -> ChangeFeedResponse | HTTPValidationError | None:
    """Get Changes

     Return entities updated, archived, or deleted since the given timestamp.

    Clients should pass ``meta.next_since`` from the previous response as
    ``since`` on each subsequent poll.  The ``since`` comparison is inclusive
    (>=) to avoid dropping events at exact timestamp boundaries; de-duplicate
    the overlap row using ``entity_id`` if needed.

    Args:
        since (datetime.datetime): ISO 8601 timestamp; changes at or after this.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChangeFeedResponse | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        since=since,
        limit=limit,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    since: datetime.datetime,
    limit: int | Unset = 50,
) -> Response[ChangeFeedResponse | HTTPValidationError]:
    """Get Changes

     Return entities updated, archived, or deleted since the given timestamp.

    Clients should pass ``meta.next_since`` from the previous response as
    ``since`` on each subsequent poll.  The ``since`` comparison is inclusive
    (>=) to avoid dropping events at exact timestamp boundaries; de-duplicate
    the overlap row using ``entity_id`` if needed.

    Args:
        since (datetime.datetime): ISO 8601 timestamp; changes at or after this.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[ChangeFeedResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        since=since,
        limit=limit,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    since: datetime.datetime,
    limit: int | Unset = 50,
) -> ChangeFeedResponse | HTTPValidationError | None:
    """Get Changes

     Return entities updated, archived, or deleted since the given timestamp.

    Clients should pass ``meta.next_since`` from the previous response as
    ``since`` on each subsequent poll.  The ``since`` comparison is inclusive
    (>=) to avoid dropping events at exact timestamp boundaries; de-duplicate
    the overlap row using ``entity_id`` if needed.

    Args:
        since (datetime.datetime): ISO 8601 timestamp; changes at or after this.
        limit (int | Unset):  Default: 50.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        ChangeFeedResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            since=since,
            limit=limit,
        )
    ).parsed
