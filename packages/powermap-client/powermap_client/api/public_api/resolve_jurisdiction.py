from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.jurisdiction_response import JurisdictionResponse
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    slug: None | str | Unset = UNSET,
    scheme: None | str | Unset = UNSET,
    value: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_slug: None | str | Unset
    if isinstance(slug, Unset):
        json_slug = UNSET
    else:
        json_slug = slug
    params["slug"] = json_slug

    json_scheme: None | str | Unset
    if isinstance(scheme, Unset):
        json_scheme = UNSET
    else:
        json_scheme = scheme
    params["scheme"] = json_scheme

    json_value: None | str | Unset
    if isinstance(value, Unset):
        json_value = UNSET
    else:
        json_value = value
    params["value"] = json_value

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/jurisdictions/resolve",
        "params": params,
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
    *,
    client: AuthenticatedClient,
    slug: None | str | Unset = UNSET,
    scheme: None | str | Unset = UNSET,
    value: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | JurisdictionResponse]:
    """Resolve Jurisdiction

     Resolve a jurisdiction by slug or by external identifier (scheme + value).

    Exactly one lookup strategy must be supplied: either ``slug`` or both
    ``scheme`` and ``value``.

    Args:
        slug (None | str | Unset):
        scheme (None | str | Unset):
        value (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionResponse]
    """

    kwargs = _get_kwargs(
        slug=slug,
        scheme=scheme,
        value=value,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    slug: None | str | Unset = UNSET,
    scheme: None | str | Unset = UNSET,
    value: None | str | Unset = UNSET,
) -> HTTPValidationError | JurisdictionResponse | None:
    """Resolve Jurisdiction

     Resolve a jurisdiction by slug or by external identifier (scheme + value).

    Exactly one lookup strategy must be supplied: either ``slug`` or both
    ``scheme`` and ``value``.

    Args:
        slug (None | str | Unset):
        scheme (None | str | Unset):
        value (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionResponse
    """

    return sync_detailed(
        client=client,
        slug=slug,
        scheme=scheme,
        value=value,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    slug: None | str | Unset = UNSET,
    scheme: None | str | Unset = UNSET,
    value: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | JurisdictionResponse]:
    """Resolve Jurisdiction

     Resolve a jurisdiction by slug or by external identifier (scheme + value).

    Exactly one lookup strategy must be supplied: either ``slug`` or both
    ``scheme`` and ``value``.

    Args:
        slug (None | str | Unset):
        scheme (None | str | Unset):
        value (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | JurisdictionResponse]
    """

    kwargs = _get_kwargs(
        slug=slug,
        scheme=scheme,
        value=value,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    slug: None | str | Unset = UNSET,
    scheme: None | str | Unset = UNSET,
    value: None | str | Unset = UNSET,
) -> HTTPValidationError | JurisdictionResponse | None:
    """Resolve Jurisdiction

     Resolve a jurisdiction by slug or by external identifier (scheme + value).

    Exactly one lookup strategy must be supplied: either ``slug`` or both
    ``scheme`` and ``value``.

    Args:
        slug (None | str | Unset):
        scheme (None | str | Unset):
        value (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | JurisdictionResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            slug=slug,
            scheme=scheme,
            value=value,
        )
    ).parsed
