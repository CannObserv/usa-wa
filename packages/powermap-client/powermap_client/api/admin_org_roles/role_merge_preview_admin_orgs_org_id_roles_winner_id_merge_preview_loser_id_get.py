from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    org_id: str,
    winner_id: str,
    loser_id: str,
    *,
    ctx: str | Unset = "",
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    params["ctx"] = ctx

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/orgs/{org_id}/roles/{winner_id}/merge-preview/{loser_id}/".format(
            org_id=quote(str(org_id), safe=""),
            winner_id=quote(str(winner_id), safe=""),
            loser_id=quote(str(loser_id), safe=""),
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
    org_id: str,
    winner_id: str,
    loser_id: str,
    *,
    client: AuthenticatedClient | Client,
    ctx: str | Unset = "",
) -> Response[Any | HTTPValidationError]:
    """Role Merge Preview

     Return the role merge-preview modal (#255).

    Roles have no names/acronyms, so this is confirmation-style: it surfaces how many
    assignments reassign vs. drop as (person, start_date) conflicts, and whether the
    loser's notes will be appended. Unlike Orgs/People — which need a curated
    `merge-with` endpoint to honour keep/drop name selections — there is nothing to
    curate here, so the modal simply posts to the existing `role_merge` (`/merge/`)
    route, targeting the roles list region. `ctx` is accepted for symmetry with the
    other entity previews; the role merge is only ever opened from the list.

    Args:
        org_id (str):
        winner_id (str):
        loser_id (str):
        ctx (str | Unset):  Default: ''.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
        winner_id=winner_id,
        loser_id=loser_id,
        ctx=ctx,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    org_id: str,
    winner_id: str,
    loser_id: str,
    *,
    client: AuthenticatedClient | Client,
    ctx: str | Unset = "",
) -> Any | HTTPValidationError | None:
    """Role Merge Preview

     Return the role merge-preview modal (#255).

    Roles have no names/acronyms, so this is confirmation-style: it surfaces how many
    assignments reassign vs. drop as (person, start_date) conflicts, and whether the
    loser's notes will be appended. Unlike Orgs/People — which need a curated
    `merge-with` endpoint to honour keep/drop name selections — there is nothing to
    curate here, so the modal simply posts to the existing `role_merge` (`/merge/`)
    route, targeting the roles list region. `ctx` is accepted for symmetry with the
    other entity previews; the role merge is only ever opened from the list.

    Args:
        org_id (str):
        winner_id (str):
        loser_id (str):
        ctx (str | Unset):  Default: ''.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        org_id=org_id,
        winner_id=winner_id,
        loser_id=loser_id,
        client=client,
        ctx=ctx,
    ).parsed


async def asyncio_detailed(
    org_id: str,
    winner_id: str,
    loser_id: str,
    *,
    client: AuthenticatedClient | Client,
    ctx: str | Unset = "",
) -> Response[Any | HTTPValidationError]:
    """Role Merge Preview

     Return the role merge-preview modal (#255).

    Roles have no names/acronyms, so this is confirmation-style: it surfaces how many
    assignments reassign vs. drop as (person, start_date) conflicts, and whether the
    loser's notes will be appended. Unlike Orgs/People — which need a curated
    `merge-with` endpoint to honour keep/drop name selections — there is nothing to
    curate here, so the modal simply posts to the existing `role_merge` (`/merge/`)
    route, targeting the roles list region. `ctx` is accepted for symmetry with the
    other entity previews; the role merge is only ever opened from the list.

    Args:
        org_id (str):
        winner_id (str):
        loser_id (str):
        ctx (str | Unset):  Default: ''.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
        winner_id=winner_id,
        loser_id=loser_id,
        ctx=ctx,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    org_id: str,
    winner_id: str,
    loser_id: str,
    *,
    client: AuthenticatedClient | Client,
    ctx: str | Unset = "",
) -> Any | HTTPValidationError | None:
    """Role Merge Preview

     Return the role merge-preview modal (#255).

    Roles have no names/acronyms, so this is confirmation-style: it surfaces how many
    assignments reassign vs. drop as (person, start_date) conflicts, and whether the
    loser's notes will be appended. Unlike Orgs/People — which need a curated
    `merge-with` endpoint to honour keep/drop name selections — there is nothing to
    curate here, so the modal simply posts to the existing `role_merge` (`/merge/`)
    route, targeting the roles list region. `ctx` is accepted for symmetry with the
    other entity previews; the role merge is only ever opened from the list.

    Args:
        org_id (str):
        winner_id (str):
        loser_id (str):
        ctx (str | Unset):  Default: ''.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            org_id=org_id,
            winner_id=winner_id,
            loser_id=loser_id,
            client=client,
            ctx=ctx,
        )
    ).parsed
