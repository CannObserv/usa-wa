from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    person_id: str,
    model_id: str,
    embedding_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/people/{person_id}/embeddings/{model_id}/{embedding_id}/vector/".format(
            person_id=quote(str(person_id), safe=""),
            model_id=quote(str(model_id), safe=""),
            embedding_id=quote(str(embedding_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | str | None:
    if response.status_code == 200:
        response_200 = response.text
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
) -> Response[HTTPValidationError | str]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    person_id: str,
    model_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | str]:
    """Embedding Vector

     Return the full pgvector literal for one embedding (copy-to-clipboard source).

    Args:
        person_id (str):
        model_id (str):
        embedding_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | str]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        model_id=model_id,
        embedding_id=embedding_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    person_id: str,
    model_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | str | None:
    """Embedding Vector

     Return the full pgvector literal for one embedding (copy-to-clipboard source).

    Args:
        person_id (str):
        model_id (str):
        embedding_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | str
    """

    return sync_detailed(
        person_id=person_id,
        model_id=model_id,
        embedding_id=embedding_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    model_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[HTTPValidationError | str]:
    """Embedding Vector

     Return the full pgvector literal for one embedding (copy-to-clipboard source).

    Args:
        person_id (str):
        model_id (str):
        embedding_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | str]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        model_id=model_id,
        embedding_id=embedding_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    model_id: str,
    embedding_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> HTTPValidationError | str | None:
    """Embedding Vector

     Return the full pgvector literal for one embedding (copy-to-clipboard source).

    Args:
        person_id (str):
        model_id (str):
        embedding_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | str
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            model_id=model_id,
            embedding_id=embedding_id,
            client=client,
        )
    ).parsed
