from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.verify_batch_request import VerifyBatchRequest
from ...models.verify_batch_response import VerifyBatchResponse
from ...types import Response


def _get_kwargs(
    *,
    body: VerifyBatchRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/people/verify-batch",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | VerifyBatchResponse | None:
    if response.status_code == 200:
        response_200 = VerifyBatchResponse.from_dict(response.json())

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
) -> Response[Any | HTTPValidationError | VerifyBatchResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: VerifyBatchRequest,
) -> Response[Any | HTTPValidationError | VerifyBatchResponse]:
    """Verify Person Batch

     Score N query embeddings against one declared candidate set (#310).

    Multi-embedding form of ``/verify`` — one group per query embedding, in
    ``embeddings`` request order, each carrying the full per-candidate result
    list with #299 semantics (candidate request order, dedup first-occurrence
    wins, ``similarity: null`` = no active enrollment, best enrollment wins
    with a deterministic id tiebreak).  One SQL round-trip regardless of N.

    422 on unknown/non-queryable model, or when any embedding's dimension
    mismatches (the detail names the failing index).

    Args:
        body (VerifyBatchRequest): Request body for POST /api/v1/people/verify-batch (#310).

            Scores N query embeddings against one declared candidate set in a single
            call — collapses the per-centroid verify loop for archival-scale jobs.
            Duplicate ``person_ids`` are deduped (first occurrence wins).  The caps
            bound the exact scoring product at 50 × 500 = 25k pairs.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | VerifyBatchResponse]
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
    body: VerifyBatchRequest,
) -> Any | HTTPValidationError | VerifyBatchResponse | None:
    """Verify Person Batch

     Score N query embeddings against one declared candidate set (#310).

    Multi-embedding form of ``/verify`` — one group per query embedding, in
    ``embeddings`` request order, each carrying the full per-candidate result
    list with #299 semantics (candidate request order, dedup first-occurrence
    wins, ``similarity: null`` = no active enrollment, best enrollment wins
    with a deterministic id tiebreak).  One SQL round-trip regardless of N.

    422 on unknown/non-queryable model, or when any embedding's dimension
    mismatches (the detail names the failing index).

    Args:
        body (VerifyBatchRequest): Request body for POST /api/v1/people/verify-batch (#310).

            Scores N query embeddings against one declared candidate set in a single
            call — collapses the per-centroid verify loop for archival-scale jobs.
            Duplicate ``person_ids`` are deduped (first occurrence wins).  The caps
            bound the exact scoring product at 50 × 500 = 25k pairs.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | VerifyBatchResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: VerifyBatchRequest,
) -> Response[Any | HTTPValidationError | VerifyBatchResponse]:
    """Verify Person Batch

     Score N query embeddings against one declared candidate set (#310).

    Multi-embedding form of ``/verify`` — one group per query embedding, in
    ``embeddings`` request order, each carrying the full per-candidate result
    list with #299 semantics (candidate request order, dedup first-occurrence
    wins, ``similarity: null`` = no active enrollment, best enrollment wins
    with a deterministic id tiebreak).  One SQL round-trip regardless of N.

    422 on unknown/non-queryable model, or when any embedding's dimension
    mismatches (the detail names the failing index).

    Args:
        body (VerifyBatchRequest): Request body for POST /api/v1/people/verify-batch (#310).

            Scores N query embeddings against one declared candidate set in a single
            call — collapses the per-centroid verify loop for archival-scale jobs.
            Duplicate ``person_ids`` are deduped (first occurrence wins).  The caps
            bound the exact scoring product at 50 × 500 = 25k pairs.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | VerifyBatchResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: VerifyBatchRequest,
) -> Any | HTTPValidationError | VerifyBatchResponse | None:
    """Verify Person Batch

     Score N query embeddings against one declared candidate set (#310).

    Multi-embedding form of ``/verify`` — one group per query embedding, in
    ``embeddings`` request order, each carrying the full per-candidate result
    list with #299 semantics (candidate request order, dedup first-occurrence
    wins, ``similarity: null`` = no active enrollment, best enrollment wins
    with a deterministic id tiebreak).  One SQL round-trip regardless of N.

    422 on unknown/non-queryable model, or when any embedding's dimension
    mismatches (the detail names the failing index).

    Args:
        body (VerifyBatchRequest): Request body for POST /api/v1/people/verify-batch (#310).

            Scores N query embeddings against one declared candidate set in a single
            call — collapses the per-centroid verify loop for archival-scale jobs.
            Duplicate ``person_ids`` are deduped (first occurrence wins).  The caps
            bound the exact scoring product at 50 × 500 = 25k pairs.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | VerifyBatchResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
