from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.identify_request import IdentifyRequest
from ...models.identify_response import IdentifyResponse
from ...types import Response


def _get_kwargs(
    *,
    body: IdentifyRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/people/identify",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | IdentifyResponse | None:
    if response.status_code == 200:
        response_200 = IdentifyResponse.from_dict(response.json())

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
) -> Response[HTTPValidationError | IdentifyResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: IdentifyRequest,
) -> Response[HTTPValidationError | IdentifyResponse]:
    """Identify Person

     Return the top-k persons whose stored embeddings best match the query vector.

    Returns ``matches: []`` when the model is unknown or has no active embeddings.
    422 when the embedding dimension does not match the model's expected dimension.

    Args:
        body (IdentifyRequest): Request body for POST /api/v1/people/identify.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | IdentifyResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    body: IdentifyRequest,
) -> HTTPValidationError | IdentifyResponse | None:
    """Identify Person

     Return the top-k persons whose stored embeddings best match the query vector.

    Returns ``matches: []`` when the model is unknown or has no active embeddings.
    422 when the embedding dimension does not match the model's expected dimension.

    Args:
        body (IdentifyRequest): Request body for POST /api/v1/people/identify.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | IdentifyResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: IdentifyRequest,
) -> Response[HTTPValidationError | IdentifyResponse]:
    """Identify Person

     Return the top-k persons whose stored embeddings best match the query vector.

    Returns ``matches: []`` when the model is unknown or has no active embeddings.
    422 when the embedding dimension does not match the model's expected dimension.

    Args:
        body (IdentifyRequest): Request body for POST /api/v1/people/identify.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | IdentifyResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: IdentifyRequest,
) -> HTTPValidationError | IdentifyResponse | None:
    """Identify Person

     Return the top-k persons whose stored embeddings best match the query vector.

    Returns ``matches: []`` when the model is unknown or has no active embeddings.
    422 when the embedding dimension does not match the model's expected dimension.

    Args:
        body (IdentifyRequest): Request body for POST /api/v1/people/identify.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | IdentifyResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
