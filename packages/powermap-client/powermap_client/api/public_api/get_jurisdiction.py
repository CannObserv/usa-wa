from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.jurisdiction_response import JurisdictionResponse
from ...types import Response


def _get_kwargs(
    jurisdiction_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/jurisdictions/{jurisdiction_id}".format(
            jurisdiction_id=quote(str(jurisdiction_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | JurisdictionResponse | None:
    if response.status_code == 200:
        response_200 = JurisdictionResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | JurisdictionResponse]:
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
) -> Response[HTTPValidationError | JurisdictionResponse]:
    """Get Jurisdiction

     Return a single jurisdiction by ULID or slug.

    Args:
        jurisdiction_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionResponse]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
) -> HTTPValidationError | JurisdictionResponse | None:
    """Get Jurisdiction

     Return a single jurisdiction by ULID or slug.

    Args:
        jurisdiction_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionResponse
    """

    return sync_detailed(
        jurisdiction_id=jurisdiction_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[HTTPValidationError | JurisdictionResponse]:
    """Get Jurisdiction

     Return a single jurisdiction by ULID or slug.

    Args:
        jurisdiction_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionResponse]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient,
) -> HTTPValidationError | JurisdictionResponse | None:
    """Get Jurisdiction

     Return a single jurisdiction by ULID or slug.

    Args:
        jurisdiction_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionResponse
    """

    return (
        await asyncio_detailed(
            jurisdiction_id=jurisdiction_id,
            client=client,
        )
    ).parsed
