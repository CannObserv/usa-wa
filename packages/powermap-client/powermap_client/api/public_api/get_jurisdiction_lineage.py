from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.jurisdiction_lineage_response import JurisdictionLineageResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    jurisdiction_id: str,
    *,
    depth: int | Unset = 10,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["depth"] = depth

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/jurisdictions/{jurisdiction_id}/lineage".format(
            jurisdiction_id=quote(str(jurisdiction_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | JurisdictionLineageResponse | None:
    if response.status_code == 200:
        response_200 = JurisdictionLineageResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | JurisdictionLineageResponse]:
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
    depth: int | Unset = 10,
) -> Response[HTTPValidationError | JurisdictionLineageResponse]:
    """Get Jurisdiction Lineage

     Return the lineage chain for a jurisdiction.

    Traverses edges with ``category = 'lineage'`` (supersedes, evolved_from,
    merged_into) in both directions up to ``depth`` hops. Cycle-safe via a
    visited array; depth capped at 50.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Args:
        jurisdiction_id (str):
        depth (int | Unset):  Default: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionLineageResponse]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
        depth=depth,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    depth: int | Unset = 10,
) -> HTTPValidationError | JurisdictionLineageResponse | None:
    """Get Jurisdiction Lineage

     Return the lineage chain for a jurisdiction.

    Traverses edges with ``category = 'lineage'`` (supersedes, evolved_from,
    merged_into) in both directions up to ``depth`` hops. Cycle-safe via a
    visited array; depth capped at 50.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Args:
        jurisdiction_id (str):
        depth (int | Unset):  Default: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionLineageResponse
    """

    return sync_detailed(
        jurisdiction_id=jurisdiction_id,
        client=client,
        depth=depth,
    ).parsed


async def asyncio_detailed(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    depth: int | Unset = 10,
) -> Response[HTTPValidationError | JurisdictionLineageResponse]:
    """Get Jurisdiction Lineage

     Return the lineage chain for a jurisdiction.

    Traverses edges with ``category = 'lineage'`` (supersedes, evolved_from,
    merged_into) in both directions up to ``depth`` hops. Cycle-safe via a
    visited array; depth capped at 50.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Args:
        jurisdiction_id (str):
        depth (int | Unset):  Default: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionLineageResponse]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
        depth=depth,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
    depth: int | Unset = 10,
) -> HTTPValidationError | JurisdictionLineageResponse | None:
    """Get Jurisdiction Lineage

     Return the lineage chain for a jurisdiction.

    Traverses edges with ``category = 'lineage'`` (supersedes, evolved_from,
    merged_into) in both directions up to ``depth`` hops. Cycle-safe via a
    visited array; depth capped at 50.

    Lookup accepts ULID or slug for ``jurisdiction_id``.

    Args:
        jurisdiction_id (str):
        depth (int | Unset):  Default: 10.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionLineageResponse
    """

    return (
        await asyncio_detailed(
            jurisdiction_id=jurisdiction_id,
            client=client,
            depth=depth,
        )
    ).parsed
