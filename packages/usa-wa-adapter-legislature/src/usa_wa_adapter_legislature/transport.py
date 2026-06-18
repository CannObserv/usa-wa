"""Transport — thin ``zeep`` wrapper for the WSL SOAP services.

A :class:`WSLClient` instance caches one ``zeep.Client`` per ``service`` name
(e.g. ``CommitteeService``, ``LegislationService``) lazily on first call and
serializes responses to plain Python dicts so downstream normalizers don't
depend on zeep's typed-object model.

Networking is delegated to ``zeep`` (which uses ``requests`` under the hood);
tests stub it out via vcrpy cassettes.
"""

from __future__ import annotations

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

    @property
    def client(self) -> Client:
        """Lazily-constructed zeep client; cached for the instance lifetime."""
        if self._client is None:
            self._client = Client(self._wsdl_url)
        return self._client

    def get_active_committees(self) -> list[dict[str, Any]]:
        """Call ``CommitteeService.GetActiveCommittees()``.

        Returns a list of plain-dict Committee rows for the *currently active*
        committees (implicit current biennium — the WSDL signature has no
        biennium parameter). The WSDL ``Committee`` complexType inherits the
        LegislativeEntity fields (Id, Name, LongName, Agency, Acronym) and adds
        Phone — zeep flattens these into one dict.
        """
        if self.service != "CommitteeService":
            raise ValueError(
                f"get_active_committees requires service='CommitteeService', got {self.service!r}"
            )
        result = self.client.service.GetActiveCommittees()
        serialized = serialize_object(result, dict)
        if serialized is None:
            return []
        return list(serialized)
