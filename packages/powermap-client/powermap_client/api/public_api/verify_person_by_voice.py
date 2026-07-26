from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.verify_request import VerifyRequest
from ...models.verify_response import VerifyResponse
from ...types import Response


def _get_kwargs(
    *,
    body: VerifyRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/people/verify",
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | VerifyResponse | None:
    if response.status_code == 200:
        response_200 = VerifyResponse.from_dict(response.json())

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
) -> Response[Any | HTTPValidationError | VerifyResponse]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    body: VerifyRequest,
) -> Response[Any | HTTPValidationError | VerifyResponse]:
    r"""Verify Person

     Score the query embedding against a declared candidate set (#299).

    Closed-set verification, not open-set identification: one result per
    requested person_id, always, in request order (duplicates deduped, first
    occurrence wins).  ``similarity`` is the best (max cosine) across the
    person's active embeddings under ``model_id``; ``embedding_id`` is the
    winning enrollment.  A person with no active embeddings — including an
    unknown or archived person id — returns ``similarity: null,
    embedding_id: null, n_embeddings: 0``.  No server-side thresholding.

    Unlike identify's ``matches: []`` convention, an unknown or non-queryable
    model is a 422 — an all-null response would be indistinguishable from
    \"no candidate has enrollments\".  422 also on dimension mismatch.

    Args:
        body (VerifyRequest): Request body for POST /api/v1/people/verify — closed-set
            verification (#299).

            Scores the query embedding against a declared candidate set instead of the
            global top-K. Duplicate ``person_ids`` are deduped (first occurrence wins);
            the cap keeps the exact per-person scan bounded while fitting a
            legislature-scale roster in one call (#310: 25 → 500).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | VerifyResponse]
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
    body: VerifyRequest,
) -> Any | HTTPValidationError | VerifyResponse | None:
    r"""Verify Person

     Score the query embedding against a declared candidate set (#299).

    Closed-set verification, not open-set identification: one result per
    requested person_id, always, in request order (duplicates deduped, first
    occurrence wins).  ``similarity`` is the best (max cosine) across the
    person's active embeddings under ``model_id``; ``embedding_id`` is the
    winning enrollment.  A person with no active embeddings — including an
    unknown or archived person id — returns ``similarity: null,
    embedding_id: null, n_embeddings: 0``.  No server-side thresholding.

    Unlike identify's ``matches: []`` convention, an unknown or non-queryable
    model is a 422 — an all-null response would be indistinguishable from
    \"no candidate has enrollments\".  422 also on dimension mismatch.

    Args:
        body (VerifyRequest): Request body for POST /api/v1/people/verify — closed-set
            verification (#299).

            Scores the query embedding against a declared candidate set instead of the
            global top-K. Duplicate ``person_ids`` are deduped (first occurrence wins);
            the cap keeps the exact per-person scan bounded while fitting a
            legislature-scale roster in one call (#310: 25 → 500).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | VerifyResponse
    """

    return sync_detailed(
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    body: VerifyRequest,
) -> Response[Any | HTTPValidationError | VerifyResponse]:
    r"""Verify Person

     Score the query embedding against a declared candidate set (#299).

    Closed-set verification, not open-set identification: one result per
    requested person_id, always, in request order (duplicates deduped, first
    occurrence wins).  ``similarity`` is the best (max cosine) across the
    person's active embeddings under ``model_id``; ``embedding_id`` is the
    winning enrollment.  A person with no active embeddings — including an
    unknown or archived person id — returns ``similarity: null,
    embedding_id: null, n_embeddings: 0``.  No server-side thresholding.

    Unlike identify's ``matches: []`` convention, an unknown or non-queryable
    model is a 422 — an all-null response would be indistinguishable from
    \"no candidate has enrollments\".  422 also on dimension mismatch.

    Args:
        body (VerifyRequest): Request body for POST /api/v1/people/verify — closed-set
            verification (#299).

            Scores the query embedding against a declared candidate set instead of the
            global top-K. Duplicate ``person_ids`` are deduped (first occurrence wins);
            the cap keeps the exact per-person scan bounded while fitting a
            legislature-scale roster in one call (#310: 25 → 500).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError | VerifyResponse]
    """

    kwargs = _get_kwargs(
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    body: VerifyRequest,
) -> Any | HTTPValidationError | VerifyResponse | None:
    r"""Verify Person

     Score the query embedding against a declared candidate set (#299).

    Closed-set verification, not open-set identification: one result per
    requested person_id, always, in request order (duplicates deduped, first
    occurrence wins).  ``similarity`` is the best (max cosine) across the
    person's active embeddings under ``model_id``; ``embedding_id`` is the
    winning enrollment.  A person with no active embeddings — including an
    unknown or archived person id — returns ``similarity: null,
    embedding_id: null, n_embeddings: 0``.  No server-side thresholding.

    Unlike identify's ``matches: []`` convention, an unknown or non-queryable
    model is a 422 — an all-null response would be indistinguishable from
    \"no candidate has enrollments\".  422 also on dimension mismatch.

    Args:
        body (VerifyRequest): Request body for POST /api/v1/people/verify — closed-set
            verification (#299).

            Scores the query embedding against a declared candidate set instead of the
            global top-K. Duplicate ``person_ids`` are deduped (first occurrence wins);
            the cap keeps the exact per-person scan bounded while fitting a
            legislature-scale roster in one call (#310: 25 → 500).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError | VerifyResponse
    """

    return (
        await asyncio_detailed(
            client=client,
            body=body,
        )
    ).parsed
