from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.contact_new_row_admin_orgs_entity_id_contacts_new_row_get_contact_type import (
    ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType,
)
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response


def _get_kwargs(
    entity_id: str,
    *,
    contact_type: ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_contact_type = contact_type.value
    params["contact_type"] = json_contact_type

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/orgs/{entity_id}/contacts/new-row/".format(
            entity_id=quote(str(entity_id), safe=""),
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
    entity_id: str,
    *,
    client: AuthenticatedClient | Client,
    contact_type: ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType,
) -> Response[Any | HTTPValidationError]:
    """Contact New Row

     Return empty contact form row for the given contact_type (email|phone).

    Args:
        entity_id (str):
        contact_type (ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        entity_id=entity_id,
        contact_type=contact_type,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    entity_id: str,
    *,
    client: AuthenticatedClient | Client,
    contact_type: ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType,
) -> Any | HTTPValidationError | None:
    """Contact New Row

     Return empty contact form row for the given contact_type (email|phone).

    Args:
        entity_id (str):
        contact_type (ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        entity_id=entity_id,
        client=client,
        contact_type=contact_type,
    ).parsed


async def asyncio_detailed(
    entity_id: str,
    *,
    client: AuthenticatedClient | Client,
    contact_type: ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType,
) -> Response[Any | HTTPValidationError]:
    """Contact New Row

     Return empty contact form row for the given contact_type (email|phone).

    Args:
        entity_id (str):
        contact_type (ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        entity_id=entity_id,
        contact_type=contact_type,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    entity_id: str,
    *,
    client: AuthenticatedClient | Client,
    contact_type: ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType,
) -> Any | HTTPValidationError | None:
    """Contact New Row

     Return empty contact form row for the given contact_type (email|phone).

    Args:
        entity_id (str):
        contact_type (ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            entity_id=entity_id,
            client=client,
            contact_type=contact_type,
        )
    ).parsed
