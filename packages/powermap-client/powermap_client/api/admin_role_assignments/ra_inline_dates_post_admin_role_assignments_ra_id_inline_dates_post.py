from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_ra_inline_dates_post_admin_role_assignments_ra_id_inline_dates_post import (
    BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost,
)
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    ra_id: str,
    *,
    body: BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/admin/role-assignments/{ra_id}/inline/dates/".format(
            ra_id=quote(str(ra_id), safe=""),
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
    ra_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Ra Inline Dates Post

     Save dates; on CHECK violation, re-render form with inline error.

    Args:
        ra_id (str):
        body (BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        ra_id=ra_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    ra_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Ra Inline Dates Post

     Save dates; on CHECK violation, re-render form with inline error.

    Args:
        ra_id (str):
        body (BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        ra_id=ra_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    ra_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Ra Inline Dates Post

     Save dates; on CHECK violation, re-render form with inline error.

    Args:
        ra_id (str):
        body (BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        ra_id=ra_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    ra_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Ra Inline Dates Post

     Save dates; on CHECK violation, re-render form with inline error.

    Args:
        ra_id (str):
        body (BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            ra_id=ra_id,
            client=client,
            body=body,
        )
    ).parsed
