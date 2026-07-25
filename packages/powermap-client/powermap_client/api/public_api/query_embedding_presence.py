from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.embedding_presence_request import EmbeddingPresenceRequest
from ...models.embedding_presence_response import EmbeddingPresenceResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    *,
    body: EmbeddingPresenceRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/people/embeddings/presence",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | EmbeddingPresenceResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EmbeddingPresenceResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = cast(Any, None)
        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | EmbeddingPresenceResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: EmbeddingPresenceRequest,
) -> Response[Any | EmbeddingPresenceResponse | HTTPValidationError]:
    """Query Embedding Presence

     Bulk enrollment-presence query (#310).

    Returns the active-embedding count under ``model_id`` for each requested
    person_id — one result per id, request order, duplicates deduped (first
    occurrence wins).  An unknown/archived person or one with no active
    enrollments returns ``n_embeddings: 0`` (no 404).  Presence exists to
    pre-filter verify candidate sets, so a non-queryable model is a 422,
    mirroring verify.

    Args:
        body (EmbeddingPresenceRequest): Request body for POST /api/v1/people/embeddings/presence
            (#310).

            Bulk enrollment-presence query: which of these person_ids have ≥1 active
            embedding under ``model_id``.  Lets callers shrink verify candidate sets
            (once per launch + periodic refresh) instead of paying request payload for
            unenrolled candidates on every verify call.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | EmbeddingPresenceResponse | HTTPValidationError]
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
    body: EmbeddingPresenceRequest,
) -> Any | EmbeddingPresenceResponse | HTTPValidationError | None:
    """Query Embedding Presence

     Bulk enrollment-presence query (#310).

    Returns the active-embedding count under ``model_id`` for each requested
    person_id — one result per id, request order, duplicates deduped (first
    occurrence wins).  An unknown/archived person or one with no active
    enrollments returns ``n_embeddings: 0`` (no 404).  Presence exists to
    pre-filter verify candidate sets, so a non-queryable model is a 422,
    mirroring verify.

    Args:
        body (EmbeddingPresenceRequest): Request body for POST /api/v1/people/embeddings/presence
            (#310).

            Bulk enrollment-presence query: which of these person_ids have ≥1 active
            embedding under ``model_id``.  Lets callers shrink verify candidate sets
            (once per launch + periodic refresh) instead of paying request payload for
            unenrolled candidates on every verify call.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | EmbeddingPresenceResponse | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: EmbeddingPresenceRequest,
) -> Response[Any | EmbeddingPresenceResponse | HTTPValidationError]:
    """Query Embedding Presence

     Bulk enrollment-presence query (#310).

    Returns the active-embedding count under ``model_id`` for each requested
    person_id — one result per id, request order, duplicates deduped (first
    occurrence wins).  An unknown/archived person or one with no active
    enrollments returns ``n_embeddings: 0`` (no 404).  Presence exists to
    pre-filter verify candidate sets, so a non-queryable model is a 422,
    mirroring verify.

    Args:
        body (EmbeddingPresenceRequest): Request body for POST /api/v1/people/embeddings/presence
            (#310).

            Bulk enrollment-presence query: which of these person_ids have ≥1 active
            embedding under ``model_id``.  Lets callers shrink verify candidate sets
            (once per launch + periodic refresh) instead of paying request payload for
            unenrolled candidates on every verify call.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | EmbeddingPresenceResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: EmbeddingPresenceRequest,
) -> Any | EmbeddingPresenceResponse | HTTPValidationError | None:
    """Query Embedding Presence

     Bulk enrollment-presence query (#310).

    Returns the active-embedding count under ``model_id`` for each requested
    person_id — one result per id, request order, duplicates deduped (first
    occurrence wins).  An unknown/archived person or one with no active
    enrollments returns ``n_embeddings: 0`` (no 404).  Presence exists to
    pre-filter verify candidate sets, so a non-queryable model is a 422,
    mirroring verify.

    Args:
        body (EmbeddingPresenceRequest): Request body for POST /api/v1/people/embeddings/presence
            (#310).

            Bulk enrollment-presence query: which of these person_ids have ≥1 active
            embedding under ``model_id``.  Lets callers shrink verify candidate sets
            (once per launch + periodic refresh) instead of paying request payload for
            unenrolled candidates on every verify call.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | EmbeddingPresenceResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
