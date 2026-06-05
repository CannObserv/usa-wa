from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.org_search_response import OrgSearchResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    q: str | Unset = "",
    limit: int | Unset = 10,
    offset: int | Unset = 0,
    include_archived: bool | Unset = False,
    identifier_type: None | str | Unset = UNSET,
    identifier_value: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["q"] = q

    params["limit"] = limit

    params["offset"] = offset

    params["include_archived"] = include_archived

    json_identifier_type: None | str | Unset
    if isinstance(identifier_type, Unset):
        json_identifier_type = UNSET
    else:
        json_identifier_type = identifier_type
    params["identifier_type"] = json_identifier_type

    json_identifier_value: None | str | Unset
    if isinstance(identifier_value, Unset):
        json_identifier_value = UNSET
    else:
        json_identifier_value = identifier_value
    params["identifier_value"] = json_identifier_value

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/orgs/search",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | OrgSearchResponse | None:
    if response.status_code == 200:
        response_200 = OrgSearchResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | OrgSearchResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    q: str | Unset = "",
    limit: int | Unset = 10,
    offset: int | Unset = 0,
    include_archived: bool | Unset = False,
    identifier_type: None | str | Unset = UNSET,
    identifier_value: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | OrgSearchResponse]:
    """Search Orgs

     Search organizations by name, acronym, or name variant.

    When identifier_type and identifier_value are both supplied they take precedence
    over q and return at most one result with has_more always false.

    Args:
        q (str | Unset):  Default: ''.
        limit (int | Unset):  Default: 10.
        offset (int | Unset):  Default: 0.
        include_archived (bool | Unset):  Default: False.
        identifier_type (None | str | Unset):
        identifier_value (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OrgSearchResponse]
    """

    kwargs = _get_kwargs(
        q=q,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
        identifier_type=identifier_type,
        identifier_value=identifier_value,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    q: str | Unset = "",
    limit: int | Unset = 10,
    offset: int | Unset = 0,
    include_archived: bool | Unset = False,
    identifier_type: None | str | Unset = UNSET,
    identifier_value: None | str | Unset = UNSET,
) -> HTTPValidationError | OrgSearchResponse | None:
    """Search Orgs

     Search organizations by name, acronym, or name variant.

    When identifier_type and identifier_value are both supplied they take precedence
    over q and return at most one result with has_more always false.

    Args:
        q (str | Unset):  Default: ''.
        limit (int | Unset):  Default: 10.
        offset (int | Unset):  Default: 0.
        include_archived (bool | Unset):  Default: False.
        identifier_type (None | str | Unset):
        identifier_value (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OrgSearchResponse
    """

    return sync_detailed(
        client=client,
        q=q,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
        identifier_type=identifier_type,
        identifier_value=identifier_value,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    q: str | Unset = "",
    limit: int | Unset = 10,
    offset: int | Unset = 0,
    include_archived: bool | Unset = False,
    identifier_type: None | str | Unset = UNSET,
    identifier_value: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | OrgSearchResponse]:
    """Search Orgs

     Search organizations by name, acronym, or name variant.

    When identifier_type and identifier_value are both supplied they take precedence
    over q and return at most one result with has_more always false.

    Args:
        q (str | Unset):  Default: ''.
        limit (int | Unset):  Default: 10.
        offset (int | Unset):  Default: 0.
        include_archived (bool | Unset):  Default: False.
        identifier_type (None | str | Unset):
        identifier_value (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OrgSearchResponse]
    """

    kwargs = _get_kwargs(
        q=q,
        limit=limit,
        offset=offset,
        include_archived=include_archived,
        identifier_type=identifier_type,
        identifier_value=identifier_value,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    q: str | Unset = "",
    limit: int | Unset = 10,
    offset: int | Unset = 0,
    include_archived: bool | Unset = False,
    identifier_type: None | str | Unset = UNSET,
    identifier_value: None | str | Unset = UNSET,
) -> HTTPValidationError | OrgSearchResponse | None:
    """Search Orgs

     Search organizations by name, acronym, or name variant.

    When identifier_type and identifier_value are both supplied they take precedence
    over q and return at most one result with has_more always false.

    Args:
        q (str | Unset):  Default: ''.
        limit (int | Unset):  Default: 10.
        offset (int | Unset):  Default: 0.
        include_archived (bool | Unset):  Default: False.
        identifier_type (None | str | Unset):
        identifier_value (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OrgSearchResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            q=q,
            limit=limit,
            offset=offset,
            include_archived=include_archived,
            identifier_type=identifier_type,
            identifier_value=identifier_value,
        )
    ).parsed
