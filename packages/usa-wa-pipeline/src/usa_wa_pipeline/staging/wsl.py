"""WSL staging rows (#306): archived SOAP wires → committees, sponsors,
committee members, meeting refs.

Each builder walks the raw store's ``latest.json`` (the newest ok wire per
resource — the file analog of the cohort providers' "latest roster per
(biennium, committee)" rule), re-parses through the adapter's offline parse
seam (:mod:`usa_wa_adapter_legislature.parsing`, injectable for tests), and
emits plain dicts. Unknown upstream fields are read with ``.get`` — never key
a parser on an exact upstream string.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_legislature import parsing
from usa_wa_adapter_legislature.adapter import (
    COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX,
    COMMITTEES_ROSTER_RESOURCE_PREFIX,
    SPONSORS_RESOURCE_PREFIX,
    parse_committee_members_hist_resource_id,
)
from usa_wa_adapter_legislature.meetings.windows import (
    COMMITTEE_MEETINGS_RESOURCE_PREFIX,
)
from usa_wa_pipeline.staging.common import latest_wires as _latest_wires
from usa_wa_pipeline.staging.common import text as _text

_MEETINGS_PREFIX = COMMITTEE_MEETINGS_RESOURCE_PREFIX

Parser = Callable[[bytes], list[dict[str, Any]]]

COMMITTEE_COLUMNS = ["biennium", "committee_id", "agency", "name", "long_name", "acronym", "phone"]
SPONSOR_COLUMNS = [
    "biennium",
    "member_id",
    "agency",
    "name",
    "long_name",
    "first_name",
    "last_name",
    "party",
    "district",
]
COMMITTEE_MEMBER_COLUMNS = [
    "biennium",
    "committee_id",
    "committee_agency",
    "committee_name",
    "member_id",
    "name",
    "long_name",
]
MEETING_COLUMNS = [
    "meeting_window",
    "meeting_agency",
    "committee_id",
    "committee_agency",
    "committee_name",
]


def committee_rows(
    store: RawStore, *, parse: Parser = parsing.parse_committees
) -> list[dict[str, Any]]:
    """``committees-roster:<biennium>`` wires → one row per (biennium, committee)."""
    rows = []
    for resource_id, wire in _latest_wires(store, COMMITTEES_ROSTER_RESOURCE_PREFIX):
        biennium = resource_id.removeprefix(COMMITTEES_ROSTER_RESOURCE_PREFIX)
        for record in parse(wire):
            rows.append(
                {
                    "biennium": biennium,
                    "committee_id": _text(record.get("Id")),
                    "agency": record.get("Agency"),
                    "name": record.get("Name"),
                    "long_name": record.get("LongName"),
                    "acronym": record.get("Acronym"),
                    "phone": record.get("Phone"),
                }
            )
    return rows


def sponsor_rows(
    store: RawStore, *, parse: Parser = parsing.parse_sponsors
) -> list[dict[str, Any]]:
    """``sponsors:<biennium>`` wires → one row per (biennium, member)."""
    rows = []
    for resource_id, wire in _latest_wires(store, SPONSORS_RESOURCE_PREFIX):
        biennium = resource_id.removeprefix(SPONSORS_RESOURCE_PREFIX)
        for record in parse(wire):
            rows.append(
                {
                    "biennium": biennium,
                    "member_id": _text(record.get("Id")),
                    "agency": record.get("Agency"),
                    "name": record.get("Name"),
                    "long_name": record.get("LongName"),
                    "first_name": record.get("FirstName"),
                    "last_name": record.get("LastName"),
                    "party": record.get("Party"),
                    "district": _text(record.get("District")),
                }
            )
    return rows


def committee_member_rows(
    store: RawStore, *, parse: Parser = parsing.parse_committee_members
) -> list[dict[str, Any]]:
    """``committee-members-hist:…`` wires → one row per (biennium, committee, member).

    The committee half of the key rides the resource id (#82); an empty wire is
    an archived benign fault (absent roster) and contributes nothing.
    """
    rows = []
    for resource_id, wire in _latest_wires(store, COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX):
        biennium, committee_id, agency, committee_name = parse_committee_members_hist_resource_id(
            resource_id
        )
        for record in parse(wire):
            rows.append(
                {
                    "biennium": biennium,
                    "committee_id": committee_id,
                    "committee_agency": agency,
                    "committee_name": committee_name,
                    "member_id": _text(record.get("Id")),
                    "name": record.get("Name"),
                    "long_name": record.get("LongName"),
                }
            )
    return rows


def meeting_rows(
    store: RawStore, *, parse: Parser = parsing.parse_committee_meetings
) -> list[dict[str, Any]]:
    """``committee-meetings:<begin>:<end>`` wires → one row per (window, committee ref).

    Flattens each meeting's nested ``Committees.Committee[]`` (zeep renders a
    single child as a dict, several as a list — the same structural rule
    ``normalize/committee_meetings.py`` documents). All agencies kept: staging
    has no policy; the Joint/`Other` filter is a downstream concern.
    """
    rows = []
    for resource_id, wire in _latest_wires(store, _MEETINGS_PREFIX):
        window = resource_id.removeprefix(_MEETINGS_PREFIX)
        for meeting in parse(wire):
            block = meeting.get("Committees") or {}
            refs = block.get("Committee") if isinstance(block, dict) else None
            if refs is None:
                continue
            for ref in [refs] if isinstance(refs, dict) else list(refs):
                rows.append(
                    {
                        "meeting_window": window,
                        "meeting_agency": meeting.get("Agency"),
                        "committee_id": _text(ref.get("Id")),
                        "committee_agency": ref.get("Agency"),
                        "committee_name": ref.get("Name"),
                    }
                )
    return rows
