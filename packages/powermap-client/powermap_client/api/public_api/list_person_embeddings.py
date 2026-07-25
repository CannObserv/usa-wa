from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.embedding_list_response import EmbeddingListResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    person_id: str,
    *,
    model_id: str,
    include_archived: bool | Unset = False,
    source_job_id: None | str | Unset = UNSET,
    source_segment: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["model_id"] = model_id

    params["include_archived"] = include_archived

    json_source_job_id: None | str | Unset
    if isinstance(source_job_id, Unset):
        json_source_job_id = UNSET
    else:
        json_source_job_id = source_job_id
    params["source_job_id"] = json_source_job_id

    json_source_segment: int | None | Unset
    if isinstance(source_segment, Unset):
        json_source_segment = UNSET
    else:
        json_source_segment = source_segment
    params["source_segment"] = json_source_segment

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/people/{person_id}/embeddings".format(
            person_id=quote(str(person_id), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | EmbeddingListResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EmbeddingListResponse.from_dict(response.json())

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
) -> Response[Any | EmbeddingListResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    person_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
    include_archived: bool | Unset = False,
    source_job_id: None | str | Unset = UNSET,
    source_segment: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[Any | EmbeddingListResponse | HTTPValidationError]:
    """List Person Embeddings

     List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.  Pass ``source_job_id``
    to restrict the list to a single provenance job (index-backed; mirrors the
    batch-delete surface) — omit it to enumerate the person's full set.  Pass
    ``source_segment`` with ``source_job_id`` to pinpoint one provenance row
    (#299 — e.g. finding the archived row behind a write 409 in a single call).
    404 if the person does not exist or is archived.

    Args:
        person_id (str):
        model_id (str):
        include_archived (bool | Unset):  Default: False.
        source_job_id (None | str | Unset):
        source_segment (int | None | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | EmbeddingListResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        model_id=model_id,
        include_archived=include_archived,
        source_job_id=source_job_id,
        source_segment=source_segment,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    person_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
    include_archived: bool | Unset = False,
    source_job_id: None | str | Unset = UNSET,
    source_segment: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Any | EmbeddingListResponse | HTTPValidationError | None:
    """List Person Embeddings

     List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.  Pass ``source_job_id``
    to restrict the list to a single provenance job (index-backed; mirrors the
    batch-delete surface) — omit it to enumerate the person's full set.  Pass
    ``source_segment`` with ``source_job_id`` to pinpoint one provenance row
    (#299 — e.g. finding the archived row behind a write 409 in a single call).
    404 if the person does not exist or is archived.

    Args:
        person_id (str):
        model_id (str):
        include_archived (bool | Unset):  Default: False.
        source_job_id (None | str | Unset):
        source_segment (int | None | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | EmbeddingListResponse | HTTPValidationError
    """

    return sync_detailed(
        person_id=person_id,
        client=client,
        model_id=model_id,
        include_archived=include_archived,
        source_job_id=source_job_id,
        source_segment=source_segment,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
    include_archived: bool | Unset = False,
    source_job_id: None | str | Unset = UNSET,
    source_segment: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[Any | EmbeddingListResponse | HTTPValidationError]:
    """List Person Embeddings

     List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.  Pass ``source_job_id``
    to restrict the list to a single provenance job (index-backed; mirrors the
    batch-delete surface) — omit it to enumerate the person's full set.  Pass
    ``source_segment`` with ``source_job_id`` to pinpoint one provenance row
    (#299 — e.g. finding the archived row behind a write 409 in a single call).
    404 if the person does not exist or is archived.

    Args:
        person_id (str):
        model_id (str):
        include_archived (bool | Unset):  Default: False.
        source_job_id (None | str | Unset):
        source_segment (int | None | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | EmbeddingListResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        model_id=model_id,
        include_archived=include_archived,
        source_job_id=source_job_id,
        source_segment=source_segment,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
    include_archived: bool | Unset = False,
    source_job_id: None | str | Unset = UNSET,
    source_segment: int | None | Unset = UNSET,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Any | EmbeddingListResponse | HTTPValidationError | None:
    """List Person Embeddings

     List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.  Pass ``source_job_id``
    to restrict the list to a single provenance job (index-backed; mirrors the
    batch-delete surface) — omit it to enumerate the person's full set.  Pass
    ``source_segment`` with ``source_job_id`` to pinpoint one provenance row
    (#299 — e.g. finding the archived row behind a write 409 in a single call).
    404 if the person does not exist or is archived.

    Args:
        person_id (str):
        model_id (str):
        include_archived (bool | Unset):  Default: False.
        source_job_id (None | str | Unset):
        source_segment (int | None | Unset):
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | EmbeddingListResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            client=client,
            model_id=model_id,
            include_archived=include_archived,
            source_job_id=source_job_id,
            source_segment=source_segment,
            limit=limit,
            offset=offset,
        )
    ).parsed
