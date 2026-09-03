"""The #302 parse seam: offline archived-wire parsers (#306).

The non-empty path needs a live WSDL binding (integration-tier, exercised
transitively by the pipeline's staging parity); what is pinned here is the
contract the pipeline depends on databaselessly — an empty wire is the archived
form of a benign fault (#82) and parses to ``[]`` without ever touching the
network, for all four operations.
"""

from usa_wa_adapter_legislature.parsing import (
    parse_committee_meetings,
    parse_committee_members,
    parse_committees,
    parse_sponsors,
)


def test_empty_wire_parses_to_empty_list_offline() -> None:
    assert parse_committees(b"") == []
    assert parse_committee_members(b"") == []
    assert parse_sponsors(b"") == []
    assert parse_committee_meetings(b"") == []
