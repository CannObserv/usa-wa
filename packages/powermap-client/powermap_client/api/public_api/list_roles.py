from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.role_list_response import RoleListResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    organization_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_organization_id: None | str | Unset
    if isinstance(organization_id, Unset):
        json_organization_id = UNSET
    else:
        json_organization_id = organization_id
    params["organization_id"] = json_organization_id

    params["include_archived"] = include_archived

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/roles",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | RoleListResponse | None:
    if response.status_code == 200:
        response_200 = RoleListResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | RoleListResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    organization_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | RoleListResponse]:
    """List Roles

     Return a paginated list of roles, optionally filtered by organization.

    Args:
        organization_id (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RoleListResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
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
    organization_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> HTTPValidationError | RoleListResponse | None:
    """List Roles

     Return a paginated list of roles, optionally filtered by organization.

    Args:
        organization_id (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RoleListResponse
    """

    return sync_detailed(
        client=client,
        organization_id=organization_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    organization_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | RoleListResponse]:
    """List Roles

     Return a paginated list of roles, optionally filtered by organization.

    Args:
        organization_id (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | RoleListResponse]
    """

    kwargs = _get_kwargs(
        organization_id=organization_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    organization_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> HTTPValidationError | RoleListResponse | None:
    """List Roles

     Return a paginated list of roles, optionally filtered by organization.

    Args:
        organization_id (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | RoleListResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            organization_id=organization_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    ).parsed
