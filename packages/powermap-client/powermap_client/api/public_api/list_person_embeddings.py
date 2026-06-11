from http import HTTPStatus
from typing import Any
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
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["model_id"] = model_id

    params["include_archived"] = include_archived

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
) -> EmbeddingListResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EmbeddingListResponse.from_dict(response.json())

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
) -> Response[EmbeddingListResponse | HTTPValidationError]:
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
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[EmbeddingListResponse | HTTPValidationError]:
    """List Person Embeddings

     List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.
    404 if the person does not exist or is archived.

    Args:
        person_id (str):
        model_id (str):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmbeddingListResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        model_id=model_id,
        include_archived=include_archived,
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
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> EmbeddingListResponse | HTTPValidationError | None:
    """List Person Embeddings

     List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.
    404 if the person does not exist or is archived.

    Args:
        person_id (str):
        model_id (str):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmbeddingListResponse | HTTPValidationError
    """

    return sync_detailed(
        person_id=person_id,
        client=client,
        model_id=model_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    *,
    client: AuthenticatedClient,
    model_id: str,
    include_archived: bool | Unset = False,
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> Response[EmbeddingListResponse | HTTPValidationError]:
    """List Person Embeddings

     List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.
    404 if the person does not exist or is archived.

    Args:
        person_id (str):
        model_id (str):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[EmbeddingListResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        model_id=model_id,
        include_archived=include_archived,
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
    limit: int | Unset = 100,
    offset: int | Unset = 0,
) -> EmbeddingListResponse | HTTPValidationError | None:
    """List Person Embeddings

     List voice embeddings for a person.

    By default returns only active (non-archived) rows.  Pass
    ``include_archived=true`` to include archived rows.
    404 if the person does not exist or is archived.

    Args:
        person_id (str):
        model_id (str):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 100.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        EmbeddingListResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            client=client,
            model_id=model_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    ).parsed
