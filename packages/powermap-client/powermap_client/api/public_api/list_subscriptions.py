from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.list_subscriptions_entity_type_type_0 import ListSubscriptionsEntityTypeType0
from ...models.subscription_list_response import SubscriptionListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    entity_type: ListSubscriptionsEntityTypeType0 | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_entity_type: None | str | Unset
    if isinstance(entity_type, Unset):
        json_entity_type = UNSET
    elif isinstance(entity_type, ListSubscriptionsEntityTypeType0):
        json_entity_type = entity_type.value
    else:
        json_entity_type = entity_type
    params["entity_type"] = json_entity_type

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/subscriptions",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | SubscriptionListResponse | None:
    if response.status_code == 200:
        response_200 = SubscriptionListResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | SubscriptionListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    entity_type: ListSubscriptionsEntityTypeType0 | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | SubscriptionListResponse]:
    """List Subscriptions

     List entity subscriptions for the calling API key.

    Args:
        entity_type (ListSubscriptionsEntityTypeType0 | None | Unset):
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SubscriptionListResponse]
    """

    kwargs = _get_kwargs(
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    entity_type: ListSubscriptionsEntityTypeType0 | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> HTTPValidationError | SubscriptionListResponse | None:
    """List Subscriptions

     List entity subscriptions for the calling API key.

    Args:
        entity_type (ListSubscriptionsEntityTypeType0 | None | Unset):
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SubscriptionListResponse
    """

    return sync_detailed(
        client=client,
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    entity_type: ListSubscriptionsEntityTypeType0 | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | SubscriptionListResponse]:
    """List Subscriptions

     List entity subscriptions for the calling API key.

    Args:
        entity_type (ListSubscriptionsEntityTypeType0 | None | Unset):
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | SubscriptionListResponse]
    """

    kwargs = _get_kwargs(
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    entity_type: ListSubscriptionsEntityTypeType0 | None | Unset = UNSET,
    limit: int | Unset = 50,
    offset: int | Unset = 0,
) -> HTTPValidationError | SubscriptionListResponse | None:
    """List Subscriptions

     List entity subscriptions for the calling API key.

    Args:
        entity_type (ListSubscriptionsEntityTypeType0 | None | Unset):
        limit (int | Unset):  Default: 50.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | SubscriptionListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            entity_type=entity_type,
            limit=limit,
            offset=offset,
        )
    ).parsed
