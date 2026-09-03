"""Offline archived-wire parsers, as module-level sync functions (#306).

The #302 pipeline's parse seam: staging models re-deserialize archived SOAP
wires through the same operation bindings the live pulls use — never a
re-implementation keyed on upstream strings, and never a data re-pull (the one
network dependency is each service's one-time WSDL GET for binding type info,
amortized across a build by the module-singleton clients). This module exists
so `usa_wa_pipeline` depends on *parsing* without importing `transport`
directly — the layer contract forbids a pipeline module naming the transport
and driving the wire; a parse-only facade that owns its client internally is
the same "provider owns its transport" seam the cohort providers use.

An empty wire is the archived form of a benign fault (an absent committee
roster, #82) — parsed as an empty list, not an error.
"""

from __future__ import annotations

import threading
from typing import Any

from usa_wa_adapter_legislature.transport import WSLClient

# One lock over every parse call: the shared WSLClient singletons are
# single-threaded by contract (transport.py), and zeep binding internals are
# not vetted for concurrency — dbt's threads:1 relies on this seam staying
# serialized even if a future consumer parallelizes (#302 CR).
_parse_lock = threading.Lock()

_committee_client = WSLClient("CommitteeService")
_meeting_client = WSLClient("CommitteeMeetingService")
_sponsor_client = WSLClient("SponsorService")


def parse_committees(wire: bytes) -> list[dict[str, Any]]:
    """Archived ``GetCommittees`` envelope → committee dicts."""
    if not wire:
        return []
    with _parse_lock:
        return _committee_client._parse_committees_sync(wire)


def parse_committee_members(wire: bytes) -> list[dict[str, Any]]:
    """Archived ``GetCommitteeMembers`` envelope → member dicts."""
    if not wire:
        return []
    with _parse_lock:
        return _committee_client._parse_historical_committee_members_sync(wire)


def parse_sponsors(wire: bytes) -> list[dict[str, Any]]:
    """Archived ``GetSponsors`` envelope → sponsor dicts."""
    if not wire:
        return []
    with _parse_lock:
        return _sponsor_client._parse_sponsors_sync(wire)


def parse_committee_meetings(wire: bytes) -> list[dict[str, Any]]:
    """Archived ``GetCommitteeMeetings`` envelope → meeting dicts."""
    if not wire:
        return []
    with _parse_lock:
        return _meeting_client._parse_committee_meetings_sync(wire)
