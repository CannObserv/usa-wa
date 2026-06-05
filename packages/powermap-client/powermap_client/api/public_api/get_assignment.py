from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.assignment_detail import AssignmentDetail
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    assignment_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/api/v1/assignments/{assignment_id}".format(
            assignment_id=quote(str(assignment_id), safe=""),
        ),
    }

    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> AssignmentDetail | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = AssignmentDetail.from_dict(response.json())

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
) -> Response[AssignmentDetail | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    assignment_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[AssignmentDetail | HTTPValidationError]:
    """Get Assignment

     Return a full assignment record with links, contact methods, and addresses.

    Args:
        assignment_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssignmentDetail | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        assignment_id=assignment_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    assignment_id: str,
    *,
    client: AuthenticatedClient,
) -> AssignmentDetail | HTTPValidationError | None:
    """Get Assignment

     Return a full assignment record with links, contact methods, and addresses.

    Args:
        assignment_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssignmentDetail | HTTPValidationError
    """

    return sync_detailed(
        assignment_id=assignment_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    assignment_id: str,
    *,
    client: AuthenticatedClient,
) -> Response[AssignmentDetail | HTTPValidationError]:
    """Get Assignment

     Return a full assignment record with links, contact methods, and addresses.

    Args:
        assignment_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[AssignmentDetail | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        assignment_id=assignment_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    assignment_id: str,
    *,
    client: AuthenticatedClient,
) -> AssignmentDetail | HTTPValidationError | None:
    """Get Assignment

     Return a full assignment record with links, contact methods, and addresses.

    Args:
        assignment_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        AssignmentDetail | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            assignment_id=assignment_id,
            client=client,
        )
    ).parsed
