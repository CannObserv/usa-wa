from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.body_identifier_edit_row_post_admin_orgs_entity_id_identifiers_ident_id_edit_row_post import (
    BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost,
)
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    entity_id: str,
    ident_id: str,
    *,
    body: BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/admin/orgs/{entity_id}/identifiers/{ident_id}/edit-row/".format(
            entity_id=quote(str(entity_id), safe=""),
            ident_id=quote(str(ident_id), safe=""),
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
    entity_id: str,
    ident_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost,
) -> Response[Any | HTTPValidationError]:
    """Identifier Edit Row Post

     Update an identifier.

    Args:
        entity_id (str):
        ident_id (str):
        body (BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        entity_id=entity_id,
        ident_id=ident_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    entity_id: str,
    ident_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost,
) -> Any | HTTPValidationError | None:
    """Identifier Edit Row Post

     Update an identifier.

    Args:
        entity_id (str):
        ident_id (str):
        body (BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        entity_id=entity_id,
        ident_id=ident_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    entity_id: str,
    ident_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost,
) -> Response[Any | HTTPValidationError]:
    """Identifier Edit Row Post

     Update an identifier.

    Args:
        entity_id (str):
        ident_id (str):
        body (BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        entity_id=entity_id,
        ident_id=ident_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    entity_id: str,
    ident_id: str,
    *,
    client: AuthenticatedClient | Client,
    body: BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost,
) -> Any | HTTPValidationError | None:
    """Identifier Edit Row Post

     Update an identifier.

    Args:
        entity_id (str):
        ident_id (str):
        body (BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            entity_id=entity_id,
            ident_id=ident_id,
            client=client,
            body=body,
        )
    ).parsed
