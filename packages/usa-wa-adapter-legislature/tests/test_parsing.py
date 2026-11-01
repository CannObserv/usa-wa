"""The #302 parse seam: offline archived-wire parsers (#306).

Pinned here, databaselessly: an empty wire is the archived form of a benign fault (#82)
and parses to ``[]``; and a non-empty wire parses with the network **blocked** (#563) —
every ``parse_*`` builds its binding from the vendored WSDL, never a live ``?wsdl`` GET,
and returns exactly the records the live-WSDL binding parses from the same envelope.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from usa_wa_adapter_legislature import parsing
from usa_wa_adapter_legislature.parsing import (
    parse_committee_meetings,
    parse_committee_members,
    parse_committees,
    parse_sponsors,
)
from usa_wa_adapter_legislature.transport import WSLClient

CASSETTE_DIR = Path(__file__).parent / "cassettes"

#: (parser, the live binding's parse of the same envelope, service, cassette): every
#: stored envelope a cassette holds for an operation the seam parses.
PARSE_CASES = [
    (
        parse_committees,
        WSLClient._parse_committees_sync,
        "CommitteeService",
        "committee_service_get_committees_2023-24.yaml",
    ),
    (
        parse_committee_members,
        WSLClient._parse_historical_committee_members_sync,
        "CommitteeService",
        "committee_service_get_committee_members_house_appropriations_2013-14.yaml",
    ),
    (
        parse_committee_members,
        WSLClient._parse_historical_committee_members_sync,
        "CommitteeService",
        "committee_service_get_committee_members_senate_ways_and_means_2013-14.yaml",
    ),
    (
        parse_sponsors,
        WSLClient._parse_sponsors_sync,
        "SponsorService",
        "sponsor_service_get_sponsors_2025-26.yaml",
    ),
    (
        parse_committee_meetings,
        WSLClient._parse_committee_meetings_sync,
        "CommitteeMeetingService",
        "committee_meeting_service_get_committee_meetings_2024-01-16.yaml",
    ),
]


def _stored_envelope(cassette: str) -> bytes:
    """The recorded operation response — the wire the raw store archives."""
    interactions = yaml.safe_load((CASSETTE_DIR / cassette).read_text())["interactions"]
    [post] = [i for i in interactions if i["request"]["method"] == "POST"]
    return post["response"]["body"]["string"].encode()


@pytest.fixture
def cold_parse_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unload the seam's module-level clients, so the parse under test builds its binding
    from scratch — a binding an earlier test loaded must not mask a live WSDL GET."""
    for client in (parsing._committee_client, parsing._meeting_client, parsing._sponsor_client):
        monkeypatch.setattr(client, "_client", None)


def test_empty_wire_parses_to_empty_list_offline() -> None:
    assert parse_committees(b"") == []
    assert parse_committee_members(b"") == []
    assert parse_sponsors(b"") == []
    assert parse_committee_meetings(b"") == []


@pytest.mark.parametrize(("parse", "live_parse", "service", "cassette"), PARSE_CASES)
def test_parses_stored_envelope_with_network_blocked(
    request: pytest.FixtureRequest,
    wsl_vcr: Any,
    parse: Callable[[bytes], list[dict[str, Any]]],
    live_parse: Callable[[WSLClient, bytes], list[dict[str, Any]]],
    service: str,
    cassette: str,
) -> None:
    wire = _stored_envelope(cassette)
    with wsl_vcr.use_cassette(cassette):
        # Today's records: the live-URL binding, its WSDL GET replayed from the cassette.
        expected = live_parse(WSLClient(service), wire)
    assert expected, "the envelope must carry rows, or equality proves nothing"

    request.getfixturevalue("cold_parse_clients")
    request.getfixturevalue("no_network")
    assert parse(wire) == expected
