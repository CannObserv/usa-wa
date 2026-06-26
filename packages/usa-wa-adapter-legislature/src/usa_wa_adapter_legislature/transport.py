"""Transport — thin ``zeep`` wrapper for the WSL SOAP services.

A :class:`WSLClient` instance caches one ``zeep.Client`` per ``service`` name
(e.g. ``CommitteeService``, ``LegislationService``) lazily on first call and
serializes responses to plain Python dicts so downstream normalizers don't
depend on zeep's typed-object model.

zeep itself is synchronous (uses ``requests`` under the hood), so the public
methods here are ``async`` and dispatch the blocking work via
``asyncio.to_thread``. Callers in async contexts (sidecar daemon, FastAPI
handlers, AdapterRunner) never block the event loop. Tests stub out the
network via vcrpy cassettes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from zeep import Client
from zeep.helpers import serialize_object

WSL_BASE_URL = "https://wslwebservices.leg.wa.gov"


class WSLClient:
    """Per-service zeep client with lazy WSDL load.

    Construct with a service name; the underlying ``zeep.Client`` is built
    on first call. Re-using a single instance amortises the WSDL fetch.
    """

    def __init__(self, service: str, *, base_url: str = WSL_BASE_URL) -> None:
        self.service = service
        self._wsdl_url = f"{base_url}/{service}.asmx?wsdl"
        self._client: Client | None = None

    def _ensure_client(self) -> Client:
        """Build the zeep client on first call; cache it for the lifetime."""
        if self._client is None:
            self._client = Client(self._wsdl_url)
        return self._client

    def _get_active_committees_sync(self) -> list[dict[str, Any]]:
        result = self._ensure_client().service.GetActiveCommittees()
        serialized = serialize_object(result, dict)
        if serialized is None:
            return []
        return list(serialized)

    def _get_committees_sync(self, biennium: str) -> list[dict[str, Any]]:
        result = self._ensure_client().service.GetCommittees(biennium)
        serialized = serialize_object(result, dict)
        if serialized is None:
            return []
        return list(serialized)

    async def get_committees(self, biennium: str) -> list[dict[str, Any]]:
        """Call ``CommitteeService.GetCommittees(biennium)`` off the event loop.

        The **parameterized historical** form of the committee pull: returns the
        flat ``Committee`` list for an explicit biennium (``"2023-24"`` style),
        as opposed to :meth:`get_active_committees`' implicit-current pull. This
        is the explicit-membership source for biennium-absence retirement (#44) —
        diffing the produced cohort against a *named* biennium's roster makes
        "absent from biennium N" a deliberate diff, not a function of run timing.

        Same dict shape (``Id, Name, LongName, Agency, Acronym, Phone``) and same
        ``asyncio.to_thread`` dispatch as :meth:`get_active_committees`.
        """
        if self.service != "CommitteeService":
            raise ValueError(
                f"get_committees requires service='CommitteeService', got {self.service!r}"
            )
        return await asyncio.to_thread(self._get_committees_sync, biennium)

    async def get_active_committees(self) -> list[dict[str, Any]]:
        """Call ``CommitteeService.GetActiveCommittees()`` off the event loop.

        Returns a list of plain-dict Committee rows for the *currently active*
        committees (implicit current biennium — the WSDL signature has no
        biennium parameter). The WSDL ``Committee`` complexType inherits the
        LegislativeEntity fields (Id, Name, LongName, Agency, Acronym) and adds
        Phone — zeep flattens these into one dict.

        Wraps zeep's synchronous SOAP call in ``asyncio.to_thread`` so the
        event loop stays responsive while WSL replies (which can take a few
        hundred ms over a cold WSDL).
        """
        if self.service != "CommitteeService":
            raise ValueError(
                f"get_active_committees requires service='CommitteeService', got {self.service!r}"
            )
        return await asyncio.to_thread(self._get_active_committees_sync)
