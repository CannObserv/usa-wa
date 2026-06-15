from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.embedding_patch_request import EmbeddingPatchRequest
from ...models.embedding_patch_response import EmbeddingPatchResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response


def _get_kwargs(
    person_id: str,
    embedding_id: str,
    *,
    body: EmbeddingPatchRequest,
    model_id: str,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    params: dict[str, Any] = {}

    params["model_id"] = model_id

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "patch",
        "url": "/api/v1/people/{person_id}/embeddings/{embedding_id}".format(
            person_id=quote(str(person_id), safe=""),
            embedding_id=quote(str(embedding_id), safe=""),
        ),
        "params": params,
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> EmbeddingPatchResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EmbeddingPatchResponse.from_dict(response.json())

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
) -> Response[EmbeddingPatchResponse | HTTPValidationError]:
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
    body: EmbeddingPatchRequest,
    model_id: str,
) -> Response[EmbeddingPatchResponse | HTTPValidationError]:
    """Patch Person Embedding

     Update mutable metadata fields on an active voice embedding.

    Only ``activity_ms``, ``audio_sample_rate_hz``, and ``recorded_at`` are
    patchable.  The embedding vector, ``model_id``, and provenance key fields
    (``source_service``, ``source_job_id``, ``source_segment``) are identity
    and cannot be changed.

    404 if the embedding is not found.
    409 if the embedding is archived (restore it first).
    422 for unknown model or if no fields are provided.

    Args:
        person_id (str):
        embedding_id (str):
        model_id (str):
        body (EmbeddingPatchRequest): Mutable metadata fields for PATCH
            /people/{id}/embeddings/{embedding_id}.

            At least one field must be provided.  The embedding vector, model_id, and
            provenance key fields (source_service, source_job_id, source_segment) are
            identity — not patchable here.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmbeddingPatchResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        embedding_id=embedding_id,
        body=body,
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
    body: EmbeddingPatchRequest,
    model_id: str,
) -> EmbeddingPatchResponse | HTTPValidationError | None:
    """Patch Person Embedding

     Update mutable metadata fields on an active voice embedding.

    Only ``activity_ms``, ``audio_sample_rate_hz``, and ``recorded_at`` are
    patchable.  The embedding vector, ``model_id``, and provenance key fields
    (``source_service``, ``source_job_id``, ``source_segment``) are identity
    and cannot be changed.

    404 if the embedding is not found.
    409 if the embedding is archived (restore it first).
    422 for unknown model or if no fields are provided.

    Args:
        person_id (str):
        embedding_id (str):
        model_id (str):
        body (EmbeddingPatchRequest): Mutable metadata fields for PATCH
            /people/{id}/embeddings/{embedding_id}.

            At least one field must be provided.  The embedding vector, model_id, and
            provenance key fields (source_service, source_job_id, source_segment) are
            identity — not patchable here.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmbeddingPatchResponse | HTTPValidationError
    """

    return sync_detailed(
        person_id=person_id,
        embedding_id=embedding_id,
        client=client,
        body=body,
        model_id=model_id,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient,
    body: EmbeddingPatchRequest,
    model_id: str,
) -> Response[EmbeddingPatchResponse | HTTPValidationError]:
    """Patch Person Embedding

     Update mutable metadata fields on an active voice embedding.

    Only ``activity_ms``, ``audio_sample_rate_hz``, and ``recorded_at`` are
    patchable.  The embedding vector, ``model_id``, and provenance key fields
    (``source_service``, ``source_job_id``, ``source_segment``) are identity
    and cannot be changed.

    404 if the embedding is not found.
    409 if the embedding is archived (restore it first).
    422 for unknown model or if no fields are provided.

    Args:
        person_id (str):
        embedding_id (str):
        model_id (str):
        body (EmbeddingPatchRequest): Mutable metadata fields for PATCH
            /people/{id}/embeddings/{embedding_id}.

            At least one field must be provided.  The embedding vector, model_id, and
            provenance key fields (source_service, source_job_id, source_segment) are
            identity — not patchable here.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmbeddingPatchResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        embedding_id=embedding_id,
        body=body,
        model_id=model_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient,
    body: EmbeddingPatchRequest,
    model_id: str,
) -> EmbeddingPatchResponse | HTTPValidationError | None:
    """Patch Person Embedding

     Update mutable metadata fields on an active voice embedding.

    Only ``activity_ms``, ``audio_sample_rate_hz``, and ``recorded_at`` are
    patchable.  The embedding vector, ``model_id``, and provenance key fields
    (``source_service``, ``source_job_id``, ``source_segment``) are identity
    and cannot be changed.

    404 if the embedding is not found.
    409 if the embedding is archived (restore it first).
    422 for unknown model or if no fields are provided.

    Args:
        person_id (str):
        embedding_id (str):
        model_id (str):
        body (EmbeddingPatchRequest): Mutable metadata fields for PATCH
            /people/{id}/embeddings/{embedding_id}.

            At least one field must be provided.  The embedding vector, model_id, and
            provenance key fields (source_service, source_job_id, source_segment) are
            identity — not patchable here.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmbeddingPatchResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            embedding_id=embedding_id,
            client=client,
            body=body,
            model_id=model_id,
        )
    ).parsed
