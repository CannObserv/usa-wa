from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    person_id: str,
    *,
    flash: None | str | Unset = UNSET,
    show_historical: bool | Unset = False,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_flash: None | str | Unset
    if isinstance(flash, Unset):
        json_flash = UNSET
    else:
        json_flash = flash
    params["flash"] = json_flash

    params["show_historical"] = show_historical

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/people/{person_id}/".format(
            person_id=quote(str(person_id), safe=""),
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
    person_id: str,
    *,
    client: AuthenticatedClient | Client,
    flash: None | str | Unset = UNSET,
    show_historical: bool | Unset = False,
) -> Response[Any | HTTPValidationError]:
    """Person Detail

     Person detail view.

    `show_historical=1` reveals legal_only / hidden rows on the names table;
    default keeps them collapsed behind the toggle (issue #123 Phase 2a Task 3).

    Args:
        person_id (str):
        flash (None | str | Unset):
        show_historical (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        flash=flash,
        show_historical=show_historical,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    person_id: str,
    *,
    client: AuthenticatedClient | Client,
    flash: None | str | Unset = UNSET,
    show_historical: bool | Unset = False,
) -> Any | HTTPValidationError | None:
    """Person Detail

     Person detail view.

    `show_historical=1` reveals legal_only / hidden rows on the names table;
    default keeps them collapsed behind the toggle (issue #123 Phase 2a Task 3).

    Args:
        person_id (str):
        flash (None | str | Unset):
        show_historical (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        person_id=person_id,
        client=client,
        flash=flash,
        show_historical=show_historical,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    *,
    client: AuthenticatedClient | Client,
    flash: None | str | Unset = UNSET,
    show_historical: bool | Unset = False,
) -> Response[Any | HTTPValidationError]:
    """Person Detail

     Person detail view.

    `show_historical=1` reveals legal_only / hidden rows on the names table;
    default keeps them collapsed behind the toggle (issue #123 Phase 2a Task 3).

    Args:
        person_id (str):
        flash (None | str | Unset):
        show_historical (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        flash=flash,
        show_historical=show_historical,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    *,
    client: AuthenticatedClient | Client,
    flash: None | str | Unset = UNSET,
    show_historical: bool | Unset = False,
) -> Any | HTTPValidationError | None:
    """Person Detail

     Person detail view.

    `show_historical=1` reveals legal_only / hidden rows on the names table;
    default keeps them collapsed behind the toggle (issue #123 Phase 2a Task 3).

    Args:
        person_id (str):
        flash (None | str | Unset):
        show_historical (bool | Unset):  Default: False.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            client=client,
            flash=flash,
            show_historical=show_historical,
        )
    ).parsed
