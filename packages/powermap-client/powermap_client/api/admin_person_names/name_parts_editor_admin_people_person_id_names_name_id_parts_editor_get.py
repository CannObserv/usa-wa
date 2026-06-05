from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import Response


def _get_kwargs(
    person_id: str,
    name_id: str,
) -> dict[str, Any]:

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/people/{person_id}/names/{name_id}/parts-editor/".format(
            person_id=quote(str(person_id), safe=""),
            name_id=quote(str(name_id), safe=""),
        ),
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
    person_id: str,
    name_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | HTTPValidationError]:
    r"""Name Parts Editor

     Return the un-suggested parts editor partial for one name row.

    Used by the \"Keep current\" button in the suggestion partial's
    confirm-before-overwrite state. Targeting `#parts-editor-{{ n.id }}`
    (just the parts editor `<details>`) leaves any in-flight edits in
    the surrounding row inputs (visibility / locale / script / sort_as /
    name / canonical / name_type) untouched.

    Args:
        person_id (str):
        name_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        name_id=name_id,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    person_id: str,
    name_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | HTTPValidationError | None:
    r"""Name Parts Editor

     Return the un-suggested parts editor partial for one name row.

    Used by the \"Keep current\" button in the suggestion partial's
    confirm-before-overwrite state. Targeting `#parts-editor-{{ n.id }}`
    (just the parts editor `<details>`) leaves any in-flight edits in
    the surrounding row inputs (visibility / locale / script / sort_as /
    name / canonical / name_type) untouched.

    Args:
        person_id (str):
        name_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        person_id=person_id,
        name_id=name_id,
        client=client,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    name_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Response[Any | HTTPValidationError]:
    r"""Name Parts Editor

     Return the un-suggested parts editor partial for one name row.

    Used by the \"Keep current\" button in the suggestion partial's
    confirm-before-overwrite state. Targeting `#parts-editor-{{ n.id }}`
    (just the parts editor `<details>`) leaves any in-flight edits in
    the surrounding row inputs (visibility / locale / script / sort_as /
    name / canonical / name_type) untouched.

    Args:
        person_id (str):
        name_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        name_id=name_id,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    name_id: str,
    *,
    client: AuthenticatedClient | Client,
) -> Any | HTTPValidationError | None:
    r"""Name Parts Editor

     Return the un-suggested parts editor partial for one name row.

    Used by the \"Keep current\" button in the suggestion partial's
    confirm-before-overwrite state. Targeting `#parts-editor-{{ n.id }}`
    (just the parts editor `<details>`) leaves any in-flight edits in
    the surrounding row inputs (visibility / locale / script / sort_as /
    name / canonical / name_type) untouched.

    Args:
        person_id (str):
        name_id (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            person_id=person_id,
            name_id=name_id,
            client=client,
        )
    ).parsed
