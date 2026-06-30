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
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from zeep import Client
from zeep.helpers import serialize_object
from zeep.transports import Transport

WSL_BASE_URL = "https://wslwebservices.leg.wa.gov"


@dataclass(frozen=True)
class WireFetch:
    """An archival fetch result: the pristine wire bytes plus the derived parse.

    ``wire`` is the raw SOAP-XML response body WSL actually sent — the provenance
    source of truth that gets archived and hashed (#54). ``records`` is the derived
    dict parse (zeep → ``serialize_object``) of whatever the operation returned —
    committee rows for ``GetActiveCommittees``, committee-bearing meetings for
    ``GetCommitteeMeetings`` — saved so the normalizer doesn't re-parse the
    envelope. Treat ``records`` as derivative: if the two ever disagree, ``wire``
    is authoritative.
    """

    records: list[dict[str, Any]]
    wire: bytes
    content_type: str


class _StoredResponse:
    """Minimal response shim for re-deserializing an **archived** SOAP envelope offline.

    ``zeep``'s ``Binding.process_reply`` reads only ``content`` / ``status_code`` / ``headers``
    off the response object — so a stored ``RawPayload.body`` can be replayed through the live
    operation binding without a network round-trip (#56's cache path). Using the *same* binding
    means the re-parse can't diverge from the live parse (a #54 provenance-fidelity concern),
    and avoids depending on ``requests``-internal mutation.

    This (and :meth:`WSLClient._parse_committee_meetings_sync`) leans on zeep internals
    (``service._binding``, ``binding.get(...)``, ``process_reply``). The regression guard is
    ``test_parse_committee_meetings_round_trips_archived_wire`` — **re-run the transport cassette
    suite after any zeep bump**; a private-API change surfaces there, not in a typecheck."""

    def __init__(self, content: bytes, *, content_type: str = "text/xml; charset=utf-8") -> None:
        self.content = content
        self.status_code = 200
        self.headers = {"Content-Type": content_type}
        self.encoding: str | None = None


class _CapturingTransport(Transport):
    """zeep transport that stashes the last operation response's raw bytes.

    SOAP operation calls route through :meth:`Transport.post`; the WSDL load uses
    ``get``/``load``. Overriding ``post`` therefore captures exactly the
    operation response envelope — the wire form we archive — without the WSDL GET
    bleeding in. Single-threaded by contract: one logical fetch at a time per
    client (the async wrappers serialize via ``asyncio.to_thread``), so the
    last-write attributes are safe to read immediately after the call returns.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_wire: bytes | None = None
        self.last_content_type: str | None = None

    def post(self, address: str, message: Any, headers: dict[str, str]) -> Any:
        response = super().post(address, message, headers)
        self.last_wire = response.content
        self.last_content_type = response.headers.get("Content-Type")
        return response


class WSLClient:
    """Per-service zeep client with lazy WSDL load.

    Construct with a service name; the underlying ``zeep.Client`` is built
    on first call. Re-using a single instance amortises the WSDL fetch.
    """

    def __init__(self, service: str, *, base_url: str = WSL_BASE_URL) -> None:
        self.service = service
        self._wsdl_url = f"{base_url}/{service}.asmx?wsdl"
        self._transport = _CapturingTransport()
        self._client: Client | None = None

    def _ensure_client(self) -> Client:
        """Build the zeep client on first call; cache it for the lifetime.

        Built on a :class:`_CapturingTransport` so the archival fetch path can
        recover the pristine response wire (#54) — a no-op cost for the
        non-archival reads that ignore it.
        """
        if self._client is None:
            self._client = Client(self._wsdl_url, transport=self._transport)
        return self._client

    def _fetch_active_committees_sync(self) -> WireFetch:
        result = self._ensure_client().service.GetActiveCommittees()
        serialized = serialize_object(result, dict)
        committees = list(serialized) if serialized is not None else []
        return WireFetch(
            records=committees,
            wire=self._transport.last_wire or b"",
            content_type=self._transport.last_content_type or "text/xml",
        )

    def _fetch_committee_meetings_sync(self, begin: datetime, end: datetime) -> WireFetch:
        result = self._ensure_client().service.GetCommitteeMeetings(beginDate=begin, endDate=end)
        serialized = serialize_object(result, dict)
        records = list(serialized) if serialized is not None else []
        return WireFetch(
            records=records,
            wire=self._transport.last_wire or b"",
            content_type=self._transport.last_content_type or "text/xml",
        )

    def _parse_committee_meetings_sync(self, wire: bytes) -> list[dict[str, Any]]:
        client = self._ensure_client()
        binding = client.service._binding
        operation = binding.get("GetCommitteeMeetings")
        result = binding.process_reply(client, operation, _StoredResponse(wire))
        serialized = serialize_object(result, dict)
        return list(serialized) if serialized is not None else []

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
        as opposed to :meth:`fetch_active_committees`' implicit-current pull. This
        is the explicit-membership source for biennium-absence retirement (#44) —
        diffing the produced cohort against a *named* biennium's roster makes
        "absent from biennium N" a deliberate diff, not a function of run timing.

        Same dict shape (``Id, Name, LongName, Agency, Acronym, Phone``) and same
        ``asyncio.to_thread`` dispatch as :meth:`fetch_active_committees`.
        """
        if self.service != "CommitteeService":
            raise ValueError(
                f"get_committees requires service='CommitteeService', got {self.service!r}"
            )
        return await asyncio.to_thread(self._get_committees_sync, biennium)

    async def fetch_active_committees(self) -> WireFetch:
        """Archival pull of the *currently active* committees, keeping the wire.

        The implicit-current-biennium pull (``GetActiveCommittees()`` — the WSDL
        signature has no biennium parameter), wrapped in ``asyncio.to_thread`` so
        the event loop stays responsive while WSL replies. Returns a
        :class:`WireFetch`: the derived ``Committee`` dicts (``Id, Name, LongName,
        Agency, Acronym, Phone`` — zeep flattens the WSDL complexType into one
        dict) plus the raw response envelope bytes for archival + hashing (#54).
        This is the form the adapter's ``fetch_one`` uses so ``RawPayload.body``
        holds what WSL sent, not our re-serialization.
        """
        if self.service != "CommitteeService":
            raise ValueError(
                f"fetch_active_committees requires service='CommitteeService', got {self.service!r}"
            )
        return await asyncio.to_thread(self._fetch_active_committees_sync)

    async def fetch_committee_meetings(self, begin: datetime, end: datetime) -> WireFetch:
        """Archival pull of committee meetings in ``[begin, end]``, keeping the wire.

        ``CommitteeMeetingService.GetCommitteeMeetings(beginDate, endDate)`` is the
        **only** source of Joint/``Other`` committee orgs (#39): each meeting carries
        a nested ``Committees.Committee[]`` list (``Id, Name, LongName, Agency,
        Acronym, Phone``). Returns a :class:`WireFetch` — the derived meeting dicts on
        ``records`` plus the raw response envelope bytes for archival + hashing (#54),
        so ``RawPayload.body`` holds what WSL sent rather than our re-serialization.
        Dedup/parenting of the committee refs is the normalizer's job; this method
        only fetches and archives. ``begin``/``end`` are UTC-naive ``datetime``s
        (WSDL ``s:dateTime``), wrapped in ``asyncio.to_thread`` like the sibling pulls.
        """
        if self.service != "CommitteeMeetingService":
            raise ValueError(
                "fetch_committee_meetings requires service='CommitteeMeetingService', "
                f"got {self.service!r}"
            )
        return await asyncio.to_thread(self._fetch_committee_meetings_sync, begin, end)

    async def parse_committee_meetings(self, wire: bytes) -> list[dict[str, Any]]:
        """Re-deserialize an **archived** ``GetCommitteeMeetings`` envelope offline (#56 cache).

        Replays a stored ``RawPayload.body`` through the **same** operation binding the live
        :meth:`fetch_committee_meetings` uses, yielding the identical derived meeting dicts —
        so #56's rename detector can read a closed window's cohort from the archive the daily
        refresh / #39 harvest already wrote, instead of re-pulling ~1.5 MB of immutable SOAP
        every weekly run. The only network cost is the one-time WSDL load (to build the
        binding's type info), not the data pull. Same ``asyncio.to_thread`` dispatch as the
        live pulls; ``wire`` is the pristine bytes (``RawPayload.body``)."""
        if self.service != "CommitteeMeetingService":
            raise ValueError(
                "parse_committee_meetings requires service='CommitteeMeetingService', "
                f"got {self.service!r}"
            )
        return await asyncio.to_thread(self._parse_committee_meetings_sync, wire)
