from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_identifier_type_edit_row_post_admin_settings_identifier_types_item_id_edit_row_post import (
    BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost,
)
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    item_id: str,
    *,
    body: BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/admin/settings/identifier-types/{item_id}/edit-row/".format(
            item_id=quote(str(item_id), safe=""),
        ),
    }

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
    item_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost,
) -> Response[Any | HTTPValidationError]:
    """Identifier Type Edit Row Post

    Args:
        item_id (str):
        body (BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        item_id=item_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    item_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost,
) -> Any | HTTPValidationError | None:
    """Identifier Type Edit Row Post

    Args:
        item_id (str):
        body (BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        item_id=item_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    item_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost,
) -> Response[Any | HTTPValidationError]:
    """Identifier Type Edit Row Post

    Args:
        item_id (str):
        body (BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        item_id=item_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    item_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost,
) -> Any | HTTPValidationError | None:
    """Identifier Type Edit Row Post

    Args:
        item_id (str):
        body (BodyIdentifierTypeEditRowPostAdminSettingsIdentifierTypesItemIdEditRowPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            item_id=item_id,
            client=client,
            body=body,
        )
    ).parsed
