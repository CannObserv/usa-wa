from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.jurisdiction_relationships_response import JurisdictionRelationshipsResponse
from ...models.list_jurisdiction_relationships_direction import ListJurisdictionRelationshipsDirection
from ...types import UNSET, Response, Unset


def _get_kwargs(
    jurisdiction_id: str,
    *,
    direction: ListJurisdictionRelationshipsDirection | Unset = ListJurisdictionRelationshipsDirection.BOTH,
    category: None | str | Unset = UNSET,
    rel_type: None | str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_direction: str | Unset = UNSET
    if not isinstance(direction, Unset):
        json_direction = direction.value

    params["direction"] = json_direction

    json_category: None | str | Unset
    if isinstance(category, Unset):
        json_category = UNSET
    else:
        json_category = category
    params["category"] = json_category

    json_rel_type: None | str | Unset
    if isinstance(rel_type, Unset):
        json_rel_type = UNSET
    else:
        json_rel_type = rel_type
    params["rel_type"] = json_rel_type

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/jurisdictions/{jurisdiction_id}/relationships".format(
            jurisdiction_id=quote(str(jurisdiction_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | JurisdictionRelationshipsResponse | None:
    if response.status_code == 200:
        response_200 = JurisdictionRelationshipsResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | JurisdictionRelationshipsResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    direction: ListJurisdictionRelationshipsDirection | Unset = ListJurisdictionRelationshipsDirection.BOTH,
    category: None | str | Unset = UNSET,
    rel_type: None | str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | JurisdictionRelationshipsResponse]:
    """List Jurisdiction Relationships

     Return relationships (edges) involving the given jurisdiction.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Args:
        jurisdiction_id (str):
        direction (ListJurisdictionRelationshipsDirection | Unset):  Default:
            ListJurisdictionRelationshipsDirection.BOTH.
        category (None | str | Unset): Filter by relationship category
        rel_type (None | str | Unset): Filter by relationship type slug
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionRelationshipsResponse]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
        direction=direction,
        category=category,
        rel_type=rel_type,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    direction: ListJurisdictionRelationshipsDirection | Unset = ListJurisdictionRelationshipsDirection.BOTH,
    category: None | str | Unset = UNSET,
    rel_type: None | str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> HTTPValidationError | JurisdictionRelationshipsResponse | None:
    """List Jurisdiction Relationships

     Return relationships (edges) involving the given jurisdiction.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Args:
        jurisdiction_id (str):
        direction (ListJurisdictionRelationshipsDirection | Unset):  Default:
            ListJurisdictionRelationshipsDirection.BOTH.
        category (None | str | Unset): Filter by relationship category
        rel_type (None | str | Unset): Filter by relationship type slug
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionRelationshipsResponse
    """

    return sync_detailed(
        jurisdiction_id=jurisdiction_id,
        client=client,
        direction=direction,
        category=category,
        rel_type=rel_type,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    direction: ListJurisdictionRelationshipsDirection | Unset = ListJurisdictionRelationshipsDirection.BOTH,
    category: None | str | Unset = UNSET,
    rel_type: None | str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[HTTPValidationError | JurisdictionRelationshipsResponse]:
    """List Jurisdiction Relationships

     Return relationships (edges) involving the given jurisdiction.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Args:
        jurisdiction_id (str):
        direction (ListJurisdictionRelationshipsDirection | Unset):  Default:
            ListJurisdictionRelationshipsDirection.BOTH.
        category (None | str | Unset): Filter by relationship category
        rel_type (None | str | Unset): Filter by relationship type slug
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionRelationshipsResponse]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
        direction=direction,
        category=category,
        rel_type=rel_type,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    direction: ListJurisdictionRelationshipsDirection | Unset = ListJurisdictionRelationshipsDirection.BOTH,
    category: None | str | Unset = UNSET,
    rel_type: None | str | Unset = UNSET,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> HTTPValidationError | JurisdictionRelationshipsResponse | None:
    """List Jurisdiction Relationships

     Return relationships (edges) involving the given jurisdiction.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Args:
        jurisdiction_id (str):
        direction (ListJurisdictionRelationshipsDirection | Unset):  Default:
            ListJurisdictionRelationshipsDirection.BOTH.
        category (None | str | Unset): Filter by relationship category
        rel_type (None | str | Unset): Filter by relationship type slug
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionRelationshipsResponse
    """

    return (
        await asyncio_detailed(
            jurisdiction_id=jurisdiction_id,
            client=client,
            direction=direction,
            category=category,
            rel_type=rel_type,
            limit=limit,
            offset=offset,
        )
    ).parsed
