from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_assignment_edit_row_post_admin_people_person_id_assignments_assignment_id_edit_row_post import (
    BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost,
)
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    person_id: str,
    assignment_id: str,
    *,
    body: BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/admin/people/{person_id}/assignments/{assignment_id}/edit-row/".format(
            person_id=quote(str(person_id), safe=""),
            assignment_id=quote(str(assignment_id), safe=""),
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
    person_id: str,
    assignment_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Assignment Edit Row Post

     Save assignment edits; return full sorted tbody.

    Args:
        person_id (str):
        assignment_id (str):
        body (BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost |
            Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        assignment_id=assignment_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    person_id: str,
    assignment_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Assignment Edit Row Post

     Save assignment edits; return full sorted tbody.

    Args:
        person_id (str):
        assignment_id (str):
        body (BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost |
            Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        person_id=person_id,
        assignment_id=assignment_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    assignment_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Assignment Edit Row Post

     Save assignment edits; return full sorted tbody.

    Args:
        person_id (str):
        assignment_id (str):
        body (BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost |
            Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        assignment_id=assignment_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    assignment_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Assignment Edit Row Post

     Save assignment edits; return full sorted tbody.

    Args:
        person_id (str):
        assignment_id (str):
        body (BodyAssignmentEditRowPostAdminPeoplePersonIdAssignmentsAssignmentIdEditRowPost |
            Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            assignment_id=assignment_id,
            client=client,
            body=body,
        )
    ).parsed
