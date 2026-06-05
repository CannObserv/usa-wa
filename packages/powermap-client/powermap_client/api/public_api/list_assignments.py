from http import HTTPStatus
from typing import Any

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.assignment_list_response import AssignmentListResponse
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    person_id: None | str | Unset = UNSET,
    role_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_person_id: None | str | Unset
    if isinstance(person_id, Unset):
        json_person_id = UNSET
    else:
        json_person_id = person_id
    params["person_id"] = json_person_id

    json_role_id: None | str | Unset
    if isinstance(role_id, Unset):
        json_role_id = UNSET
    else:
        json_role_id = role_id
    params["role_id"] = json_role_id

    params["include_archived"] = include_archived

    params["limit"] = limit

    params["offset"] = offset

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/assignments",
        "params": params,
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AssignmentListResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AssignmentListResponse.from_dict(response.json())

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
) -> Response[AssignmentListResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient,
    person_id: None | str | Unset = UNSET,
    role_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[AssignmentListResponse | HTTPValidationError]:
    """List Assignments

     Return a paginated list of role assignments, optionally filtered.

    Args:
        person_id (None | str | Unset):
        role_id (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssignmentListResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        role_id=role_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    *,
    client: AuthenticatedClient,
    person_id: None | str | Unset = UNSET,
    role_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> AssignmentListResponse | HTTPValidationError | None:
    """List Assignments

     Return a paginated list of role assignments, optionally filtered.

    Args:
        person_id (None | str | Unset):
        role_id (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssignmentListResponse | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        person_id=person_id,
        role_id=role_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient,
    person_id: None | str | Unset = UNSET,
    role_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> Response[AssignmentListResponse | HTTPValidationError]:
    """List Assignments

     Return a paginated list of role assignments, optionally filtered.

    Args:
        person_id (None | str | Unset):
        role_id (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssignmentListResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        role_id=role_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    *,
    client: AuthenticatedClient,
    person_id: None | str | Unset = UNSET,
    role_id: None | str | Unset = UNSET,
    include_archived: bool | Unset = False,
    limit: int | Unset = 20,
    offset: int | Unset = 0,
) -> AssignmentListResponse | HTTPValidationError | None:
    """List Assignments

     Return a paginated list of role assignments, optionally filtered.

    Args:
        person_id (None | str | Unset):
        role_id (None | str | Unset):
        include_archived (bool | Unset):  Default: False.
        limit (int | Unset):  Default: 20.
        offset (int | Unset):  Default: 0.

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssignmentListResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            person_id=person_id,
            role_id=role_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
    ).parsed
