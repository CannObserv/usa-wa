from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_person_merge_with_admin_people_winner_id_merge_with_loser_id_post import (
    BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost,
)
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    winner_id: str,
    loser_id: str,
    *,
    body: BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/admin/people/{winner_id}/merge-with/{loser_id}/".format(
            winner_id=quote(str(winner_id), safe=""),
            loser_id=quote(str(loser_id), safe=""),
        ),
    }

    if not isinstance(body, Unset):
        _kwargs["data"] = body.to_dict()
    headers["Content-Type"] = "application/x-www-form-urlencoded"

    _kwargs["headers"] = headers
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
    winner_id: str,
    loser_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    r"""Person Merge With

     Curated person merge from the preview modal (#255).

    `keep_name_ids` is authoritative — only the checked loser names transfer (the
    rest are dropped). `return_to=\"list\"` re-renders the people list region in place;
    otherwise HX-Redirect to the winner detail page.

    Args:
        winner_id (str):
        loser_id (str):
        body (BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        winner_id=winner_id,
        loser_id=loser_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    winner_id: str,
    loser_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    r"""Person Merge With

     Curated person merge from the preview modal (#255).

    `keep_name_ids` is authoritative — only the checked loser names transfer (the
    rest are dropped). `return_to=\"list\"` re-renders the people list region in place;
    otherwise HX-Redirect to the winner detail page.

    Args:
        winner_id (str):
        loser_id (str):
        body (BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        winner_id=winner_id,
        loser_id=loser_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    winner_id: str,
    loser_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    r"""Person Merge With

     Curated person merge from the preview modal (#255).

    `keep_name_ids` is authoritative — only the checked loser names transfer (the
    rest are dropped). `return_to=\"list\"` re-renders the people list region in place;
    otherwise HX-Redirect to the winner detail page.

    Args:
        winner_id (str):
        loser_id (str):
        body (BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        winner_id=winner_id,
        loser_id=loser_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    winner_id: str,
    loser_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    r"""Person Merge With

     Curated person merge from the preview modal (#255).

    `keep_name_ids` is authoritative — only the checked loser names transfer (the
    rest are dropped). `return_to=\"list\"` re-renders the people list region in place;
    otherwise HX-Redirect to the winner detail page.

    Args:
        winner_id (str):
        loser_id (str):
        body (BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            winner_id=winner_id,
            loser_id=loser_id,
            client=client,
            body=body,
        )
    ).parsed
