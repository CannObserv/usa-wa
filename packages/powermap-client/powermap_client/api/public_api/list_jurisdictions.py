from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.jurisdiction_list_response import JurisdictionListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    type_: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_type_: None | str | Unset
    if isinstance(type_, Unset):
        json_type_ = UNSET
    else:
        json_type_ = type_
    params["type"] = json_type_

    params["include_archived"] = include_archived

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/jurisdictions",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | JurisdictionListResponse | None:
    if response.status_code == 200:
        response_200 = JurisdictionListResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | JurisdictionListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    type_: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | JurisdictionListResponse]:
    """List Jurisdictions

     Return a paginated list of jurisdictions.

    Args:
        type_ (None | str | Unset): Filter by type slug
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionListResponse]
    """

    kwargs = _get_kwargs(
        type_=type_,
        include_archived=include_archived,
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
    type_: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> HTTPValidationError | JurisdictionListResponse | None:
    """List Jurisdictions

     Return a paginated list of jurisdictions.

    Args:
        type_ (None | str | Unset): Filter by type slug
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionListResponse
    """

    return sync_detailed(
        client=client,
        type_=type_,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    type_: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | JurisdictionListResponse]:
    """List Jurisdictions

     Return a paginated list of jurisdictions.

    Args:
        type_ (None | str | Unset): Filter by type slug
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionListResponse]
    """

    kwargs = _get_kwargs(
        type_=type_,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    type_: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> HTTPValidationError | JurisdictionListResponse | None:
    """List Jurisdictions

     Return a paginated list of jurisdictions.

    Args:
        type_ (None | str | Unset): Filter by type slug
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            type_=type_,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    ).parsed
