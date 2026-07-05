from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    group: None | str | Unset = UNSET,
    key: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    disposition: None | str | Unset = UNSET,
    show_empty: bool | Unset = False,
    q: None | str | Unset = UNSET,
    window: str | Unset = "24h",
    page: int | Unset = 1,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_group: None | str | Unset
    if isinstance(group, Unset):
        json_group = UNSET
    else:
        json_group = group
    params["group"] = json_group

    json_key: None | str | Unset
    if isinstance(key, Unset):
        json_key = UNSET
    else:
        json_key = key
    params["key"] = json_key

    json_status: None | str | Unset
    if isinstance(status, Unset):
        json_status = UNSET
    else:
        json_status = status
    params["status"] = json_status

    json_disposition: None | str | Unset
    if isinstance(disposition, Unset):
        json_disposition = UNSET
    else:
        json_disposition = disposition
    params["disposition"] = json_disposition

    params["show_empty"] = show_empty

    json_q: None | str | Unset
    if isinstance(q, Unset):
        json_q = UNSET
    else:
        json_q = q
    params["q"] = json_q

    params["window"] = window

    params["page"] = page

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/activity/requests/",
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
    *,
    client: AuthenticatedClient | Client,
    group: None | str | Unset = UNSET,
    key: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    disposition: None | str | Unset = UNSET,
    show_empty: bool | Unset = False,
    q: None | str | Unset = UNSET,
    window: str | Unset = "24h",
    page: int | Unset = 1,
) -> Response[Any | HTTPValidationError]:
    """Request List

     Filterable, paginated list of captured API requests.

    Args:
        group (None | str | Unset):
        key (None | str | Unset):
        status (None | str | Unset):
        disposition (None | str | Unset):
        show_empty (bool | Unset):  Default: False.
        q (None | str | Unset):
        window (str | Unset):  Default: '24h'.
        page (int | Unset):  Default: 1.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        group=group,
        key=key,
        status=status,
        disposition=disposition,
        show_empty=show_empty,
        q=q,
        window=window,
        page=page,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient | Client,
    group: None | str | Unset = UNSET,
    key: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    disposition: None | str | Unset = UNSET,
    show_empty: bool | Unset = False,
    q: None | str | Unset = UNSET,
    window: str | Unset = "24h",
    page: int | Unset = 1,
) -> Any | HTTPValidationError | None:
    """Request List

     Filterable, paginated list of captured API requests.

    Args:
        group (None | str | Unset):
        key (None | str | Unset):
        status (None | str | Unset):
        disposition (None | str | Unset):
        show_empty (bool | Unset):  Default: False.
        q (None | str | Unset):
        window (str | Unset):  Default: '24h'.
        page (int | Unset):  Default: 1.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        group=group,
        key=key,
        status=status,
        disposition=disposition,
        show_empty=show_empty,
        q=q,
        window=window,
        page=page,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    group: None | str | Unset = UNSET,
    key: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    disposition: None | str | Unset = UNSET,
    show_empty: bool | Unset = False,
    q: None | str | Unset = UNSET,
    window: str | Unset = "24h",
    page: int | Unset = 1,
) -> Response[Any | HTTPValidationError]:
    """Request List

     Filterable, paginated list of captured API requests.

    Args:
        group (None | str | Unset):
        key (None | str | Unset):
        status (None | str | Unset):
        disposition (None | str | Unset):
        show_empty (bool | Unset):  Default: False.
        q (None | str | Unset):
        window (str | Unset):  Default: '24h'.
        page (int | Unset):  Default: 1.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        group=group,
        key=key,
        status=status,
        disposition=disposition,
        show_empty=show_empty,
        q=q,
        window=window,
        page=page,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    group: None | str | Unset = UNSET,
    key: None | str | Unset = UNSET,
    status: None | str | Unset = UNSET,
    disposition: None | str | Unset = UNSET,
    show_empty: bool | Unset = False,
    q: None | str | Unset = UNSET,
    window: str | Unset = "24h",
    page: int | Unset = 1,
) -> Any | HTTPValidationError | None:
    """Request List

     Filterable, paginated list of captured API requests.

    Args:
        group (None | str | Unset):
        key (None | str | Unset):
        status (None | str | Unset):
        disposition (None | str | Unset):
        show_empty (bool | Unset):  Default: False.
        q (None | str | Unset):
        window (str | Unset):  Default: '24h'.
        page (int | Unset):  Default: 1.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            group=group,
            key=key,
            status=status,
            disposition=disposition,
            show_empty=show_empty,
            q=q,
            window=window,
            page=page,
        )
    ).parsed
