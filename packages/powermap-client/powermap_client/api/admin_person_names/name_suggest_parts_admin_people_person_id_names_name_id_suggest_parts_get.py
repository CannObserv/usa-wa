from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    person_id: str,
    name_id: str,
    *,
    confirm: None | str | Unset = UNSET,
) -> dict[str, Any]:

    params: dict[str, Any] = {}

    json_confirm: None | str | Unset
    if isinstance(confirm, Unset):
        json_confirm = UNSET
    else:
        json_confirm = confirm
    params["confirm"] = json_confirm

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/admin/people/{person_id}/names/{name_id}/suggest-parts/".format(
            person_id=quote(str(person_id), safe=""),
            name_id=quote(str(name_id), safe=""),
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
    person_id: str,
    name_id: str,
    *,
    client: AuthenticatedClient | Client,
    confirm: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Name Suggest Parts

     Return the parts editor partial pre-populated from `suggest_parts`.

    Branches the response into three UX states:

    1. **Confirm-before-overwrite** — when the row already has a parts
       sidecar AND the request did not pass ``confirm=1``. The partial
       carries a small Replace / Keep current form instead of clobbering
       prior operator edits. Replace re-issues the GET with ``confirm=1``.
    2. **Advisory only** — empty/whitespace name, NULL script, or
       ``name_type`` in the non-decomposable set. Suggestion bucket is
       always ``skip``; the partial renders with empty inputs + a small
       reason line so the operator understands why nothing pre-filled.
    3. **Pre-fill** — for ``trivial`` / ``ambiguous`` buckets when no
       existing parts (or the confirm flag is present). Inputs are
       populated from the suggestion; the advisory line surfaces
       confidence + reasons.

    Args:
        person_id (str):
        name_id (str):
        confirm (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        name_id=name_id,
        confirm=confirm,
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
    confirm: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Name Suggest Parts

     Return the parts editor partial pre-populated from `suggest_parts`.

    Branches the response into three UX states:

    1. **Confirm-before-overwrite** — when the row already has a parts
       sidecar AND the request did not pass ``confirm=1``. The partial
       carries a small Replace / Keep current form instead of clobbering
       prior operator edits. Replace re-issues the GET with ``confirm=1``.
    2. **Advisory only** — empty/whitespace name, NULL script, or
       ``name_type`` in the non-decomposable set. Suggestion bucket is
       always ``skip``; the partial renders with empty inputs + a small
       reason line so the operator understands why nothing pre-filled.
    3. **Pre-fill** — for ``trivial`` / ``ambiguous`` buckets when no
       existing parts (or the confirm flag is present). Inputs are
       populated from the suggestion; the advisory line surfaces
       confidence + reasons.

    Args:
        person_id (str):
        name_id (str):
        confirm (None | str | Unset):

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
        confirm=confirm,
    ).parsed


async def asyncio_detailed(
    person_id: str,
    name_id: str,
    *,
    client: AuthenticatedClient | Client,
    confirm: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Name Suggest Parts

     Return the parts editor partial pre-populated from `suggest_parts`.

    Branches the response into three UX states:

    1. **Confirm-before-overwrite** — when the row already has a parts
       sidecar AND the request did not pass ``confirm=1``. The partial
       carries a small Replace / Keep current form instead of clobbering
       prior operator edits. Replace re-issues the GET with ``confirm=1``.
    2. **Advisory only** — empty/whitespace name, NULL script, or
       ``name_type`` in the non-decomposable set. Suggestion bucket is
       always ``skip``; the partial renders with empty inputs + a small
       reason line so the operator understands why nothing pre-filled.
    3. **Pre-fill** — for ``trivial`` / ``ambiguous`` buckets when no
       existing parts (or the confirm flag is present). Inputs are
       populated from the suggestion; the advisory line surfaces
       confidence + reasons.

    Args:
        person_id (str):
        name_id (str):
        confirm (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        person_id=person_id,
        name_id=name_id,
        confirm=confirm,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    person_id: str,
    name_id: str,
    *,
    client: AuthenticatedClient | Client,
    confirm: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Name Suggest Parts

     Return the parts editor partial pre-populated from `suggest_parts`.

    Branches the response into three UX states:

    1. **Confirm-before-overwrite** — when the row already has a parts
       sidecar AND the request did not pass ``confirm=1``. The partial
       carries a small Replace / Keep current form instead of clobbering
       prior operator edits. Replace re-issues the GET with ``confirm=1``.
    2. **Advisory only** — empty/whitespace name, NULL script, or
       ``name_type`` in the non-decomposable set. Suggestion bucket is
       always ``skip``; the partial renders with empty inputs + a small
       reason line so the operator understands why nothing pre-filled.
    3. **Pre-fill** — for ``trivial`` / ``ambiguous`` buckets when no
       existing parts (or the confirm flag is present). Inputs are
       populated from the suggestion; the advisory line surfaces
       confidence + reasons.

    Args:
        person_id (str):
        name_id (str):
        confirm (None | str | Unset):

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
            confirm=confirm,
        )
    ).parsed
