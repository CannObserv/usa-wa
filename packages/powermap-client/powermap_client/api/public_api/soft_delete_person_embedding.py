from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.embedding_archive_response import EmbeddingArchiveResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response


def _get_kwargs(
    person_id: str,
    embedding_id: str,
    *,
    model_id: str,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["model_id"] = model_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/api/v1/people/{person_id}/embeddings/{embedding_id}".format(
            person_id=quote(str(person_id), safe=""),
            embedding_id=quote(str(embedding_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EmbeddingArchiveResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EmbeddingArchiveResponse.from_dict(response.json())

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
) -> Response[EmbeddingArchiveResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    person_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
) -> Response[EmbeddingArchiveResponse | HTTPValidationError]:
    """Soft Delete Embedding

     Soft-delete a single embedding row by setting ``archived_at``.

    Idempotent — re-deleting an already-archived row returns 200 with the
    existing ``archived_at``.  404 if the embedding or person is not found.

    Args:
        person_id (str):
        embedding_id (str):
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmbeddingArchiveResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        embedding_id=embedding_id,
        model_id=model_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    person_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
) -> EmbeddingArchiveResponse | HTTPValidationError | None:
    """Soft Delete Embedding

     Soft-delete a single embedding row by setting ``archived_at``.

    Idempotent — re-deleting an already-archived row returns 200 with the
    existing ``archived_at``.  404 if the embedding or person is not found.

    Args:
        person_id (str):
        embedding_id (str):
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmbeddingArchiveResponse | HTTPValidationError
    """

    return sync_detailed(
        person_id=person_id,
        embedding_id=embedding_id,
        client=client,
        model_id=model_id,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
) -> Response[EmbeddingArchiveResponse | HTTPValidationError]:
    """Soft Delete Embedding

     Soft-delete a single embedding row by setting ``archived_at``.

    Idempotent — re-deleting an already-archived row returns 200 with the
    existing ``archived_at``.  404 if the embedding or person is not found.

    Args:
        person_id (str):
        embedding_id (str):
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmbeddingArchiveResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        embedding_id=embedding_id,
        model_id=model_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
) -> EmbeddingArchiveResponse | HTTPValidationError | None:
    """Soft Delete Embedding

     Soft-delete a single embedding row by setting ``archived_at``.

    Idempotent — re-deleting an already-archived row returns 200 with the
    existing ``archived_at``.  404 if the embedding or person is not found.

    Args:
        person_id (str):
        embedding_id (str):
        model_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmbeddingArchiveResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            embedding_id=embedding_id,
            client=client,
            model_id=model_id,
        )
    ).parsed
