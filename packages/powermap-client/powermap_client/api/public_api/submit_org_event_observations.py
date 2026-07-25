from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.event_observations_response import EventObservationsResponse
from ...models.http_validation_error import HTTPValidationError
from ...models.org_event_observations_request import OrgEventObservationsRequest
from ...types import Response


def _get_kwargs(
    org_id: str,
    *,
    body: OrgEventObservationsRequest,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}

    _kwargs: dict[str, Any] = {
        "method": "post",
        "url": "/api/v1/orgs/{org_id}/events/observations".format(
            org_id=quote(str(org_id), safe=""),
        ),
    }

    _kwargs["json"] = body.to_dict()

    headers["Content-Type"] = "application/json"

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | EventObservationsResponse | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = EventObservationsResponse.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if response.status_code == 429:
        response_429 = cast(Any, None)
        return response_429

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[Any | EventObservationsResponse | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    org_id: str,
    *,
    client: AuthenticatedClient,
    body: OrgEventObservationsRequest,
) -> Response[Any | EventObservationsResponse | HTTPValidationError]:
    r"""Submit Org Event Observations

     Observe lifecycle events on an org, **partial-success** (#321/#322).

    The event-native producer surface: each event lands independently under its
    own savepoint, so one rejected event (e.g. a ``succeeded_by`` whose successor
    isn't anchored yet → ``linked_entity_unresolved``) never rolls back its
    siblings. ``pm_event_id`` refines an event in place; absent it, a natural
    create with content dedup. ``op=\"retract\"`` archives the ``pm_event_id``
    event — the only correction for a dateless linked event, so a re-link is
    create-new + retract-old in one batch (#322). Returns per-event dispositions
    + reason slugs.

    Args:
        org_id (str):
        body (OrgEventObservationsRequest): Payload for POST
            /api/v1/orgs/{org_id}/events/observations (#321).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | EventObservationsResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
        body=body,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    org_id: str,
    *,
    client: AuthenticatedClient,
    body: OrgEventObservationsRequest,
) -> Any | EventObservationsResponse | HTTPValidationError | None:
    r"""Submit Org Event Observations

     Observe lifecycle events on an org, **partial-success** (#321/#322).

    The event-native producer surface: each event lands independently under its
    own savepoint, so one rejected event (e.g. a ``succeeded_by`` whose successor
    isn't anchored yet → ``linked_entity_unresolved``) never rolls back its
    siblings. ``pm_event_id`` refines an event in place; absent it, a natural
    create with content dedup. ``op=\"retract\"`` archives the ``pm_event_id``
    event — the only correction for a dateless linked event, so a re-link is
    create-new + retract-old in one batch (#322). Returns per-event dispositions
    + reason slugs.

    Args:
        org_id (str):
        body (OrgEventObservationsRequest): Payload for POST
            /api/v1/orgs/{org_id}/events/observations (#321).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | EventObservationsResponse | HTTPValidationError
    """

    return sync_detailed(
        org_id=org_id,
        client=client,
        body=body,
    ).parsed


async def asyncio_detailed(
    org_id: str,
    *,
    client: AuthenticatedClient,
    body: OrgEventObservationsRequest,
) -> Response[Any | EventObservationsResponse | HTTPValidationError]:
    r"""Submit Org Event Observations

     Observe lifecycle events on an org, **partial-success** (#321/#322).

    The event-native producer surface: each event lands independently under its
    own savepoint, so one rejected event (e.g. a ``succeeded_by`` whose successor
    isn't anchored yet → ``linked_entity_unresolved``) never rolls back its
    siblings. ``pm_event_id`` refines an event in place; absent it, a natural
    create with content dedup. ``op=\"retract\"`` archives the ``pm_event_id``
    event — the only correction for a dateless linked event, so a re-link is
    create-new + retract-old in one batch (#322). Returns per-event dispositions
    + reason slugs.

    Args:
        org_id (str):
        body (OrgEventObservationsRequest): Payload for POST
            /api/v1/orgs/{org_id}/events/observations (#321).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | EventObservationsResponse | HTTPValidationError]
    """

    kwargs = _get_kwargs(
        org_id=org_id,
        body=body,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    org_id: str,
    *,
    client: AuthenticatedClient,
    body: OrgEventObservationsRequest,
) -> Any | EventObservationsResponse | HTTPValidationError | None:
    r"""Submit Org Event Observations

     Observe lifecycle events on an org, **partial-success** (#321/#322).

    The event-native producer surface: each event lands independently under its
    own savepoint, so one rejected event (e.g. a ``succeeded_by`` whose successor
    isn't anchored yet → ``linked_entity_unresolved``) never rolls back its
    siblings. ``pm_event_id`` refines an event in place; absent it, a natural
    create with content dedup. ``op=\"retract\"`` archives the ``pm_event_id``
    event — the only correction for a dateless linked event, so a re-link is
    create-new + retract-old in one batch (#322). Returns per-event dispositions
    + reason slugs.

    Args:
        org_id (str):
        body (OrgEventObservationsRequest): Payload for POST
            /api/v1/orgs/{org_id}/events/observations (#321).

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | EventObservationsResponse | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            org_id=org_id,
            client=client,
            body=body,
        )
    ).parsed
