from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    id_a: str,
    id_b: str,
    *,
    winner: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_winner: None | str | Unset
    if isinstance(winner, Unset):
        json_winner = UNSET
    else:
        json_winner = winner
    params["winner"] = json_winner

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/orgs/{id_a}/merge-preview/{id_b}/".format(
            id_a=quote(str(id_a), safe=""),
            id_b=quote(str(id_b), safe=""),
        ),
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = response.json()
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
) -> Response[Any | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    id_a: str,
    id_b: str,
    *,
    client: AuthenticatedClient | Client,
    winner: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Org Merge Preview

     Return preview modal: impact of merging id_b into id_a (or flipped via ?winner=).

    Args:
        id_a (str):
        id_b (str):
        winner (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        id_a=id_a,
        id_b=id_b,
        winner=winner,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    id_a: str,
    id_b: str,
    *,
    client: AuthenticatedClient | Client,
    winner: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Org Merge Preview

     Return preview modal: impact of merging id_b into id_a (or flipped via ?winner=).

    Args:
        id_a (str):
        id_b (str):
        winner (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        id_a=id_a,
        id_b=id_b,
        client=client,
        winner=winner,
    ).parsed


async def asyncio_detailed(
    id_a: str,
    id_b: str,
    *,
    client: AuthenticatedClient | Client,
    winner: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Org Merge Preview

     Return preview modal: impact of merging id_b into id_a (or flipped via ?winner=).

    Args:
        id_a (str):
        id_b (str):
        winner (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        id_a=id_a,
        id_b=id_b,
        winner=winner,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    id_a: str,
    id_b: str,
    *,
    client: AuthenticatedClient | Client,
    winner: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Org Merge Preview

     Return preview modal: impact of merging id_b into id_a (or flipped via ?winner=).

    Args:
        id_a (str):
        id_b (str):
        winner (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            id_a=id_a,
            id_b=id_b,
            client=client,
            winner=winner,
        )
    ).parsed
