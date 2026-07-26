from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    jurisdiction_id: str,
    *,
    flash: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_flash: None | str | Unset
    if isinstance(flash, Unset):
        json_flash = UNSET
    else:
        json_flash = flash
    params["flash"] = json_flash

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/jurisdictions/{jurisdiction_id}/".format(
            jurisdiction_id=quote(str(jurisdiction_id), safe=""),
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
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient | Client,
    flash: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Jurisdiction Detail

     Jurisdiction detail view.

    Args:
        jurisdiction_id (str):
        flash (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
        flash=flash,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient | Client,
    flash: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Jurisdiction Detail

     Jurisdiction detail view.

    Args:
        jurisdiction_id (str):
        flash (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        jurisdiction_id=jurisdiction_id,
        client=client,
        flash=flash,
    ).parsed


async def asyncio_detailed(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient | Client,
    flash: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Jurisdiction Detail

     Jurisdiction detail view.

    Args:
        jurisdiction_id (str):
        flash (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        jurisdiction_id=jurisdiction_id,
        flash=flash,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    jurisdiction_id: str,
    *,
    client: AuthenticatedClient | Client,
    flash: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Jurisdiction Detail

     Jurisdiction detail view.

    Args:
        jurisdiction_id (str):
        flash (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            jurisdiction_id=jurisdiction_id,
            client=client,
            flash=flash,
        )
    ).parsed
