"""WSL staging rows (#306): archived SOAP wires → committees, sponsors,
committee members, meeting refs.

Each builder walks the raw store's ``latest.json`` (the newest ok wire per
resource — the file analog of the deleted cohort providers' "latest roster per
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
from usa_wa_adapter_legislature.meetings.windows import (
    COMMITTEE_MEETINGS_RESOURCE_PREFIX,
)
from usa_wa_adapter_legislature.resources import (
    COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX,
    COMMITTEES_ROSTER_RESOURCE_PREFIX,
    SPONSORS_RESOURCE_PREFIX,
    parse_committee_members_hist_resource_id,
)
from usa_wa_pipeline.staging.common import PROVENANCE_SCHEMA, first_fetches
from usa_wa_pipeline.staging.common import latest_wires as _latest_wires
from usa_wa_pipeline.staging.common import provenance as _provenance
from usa_wa_pipeline.staging.common import text as _text

_MEETINGS_PREFIX = COMMITTEE_MEETINGS_RESOURCE_PREFIX

Parser = Callable[[bytes], list[dict[str, Any]]]

COMMITTEE_SCHEMA = {
    "biennium": "VARCHAR",
    "committee_id": "VARCHAR",
    "agency": "VARCHAR",
    "name": "VARCHAR",
    "long_name": "VARCHAR",
    "acronym": "VARCHAR",
    "phone": "VARCHAR",
    **PROVENANCE_SCHEMA,
}
COMMITTEE_COLUMNS = list(COMMITTEE_SCHEMA)
SPONSOR_SCHEMA = {
    "biennium": "VARCHAR",
    "member_id": "VARCHAR",
    "agency": "VARCHAR",
    "name": "VARCHAR",
    "long_name": "VARCHAR",
    "first_name": "VARCHAR",
    "last_name": "VARCHAR",
    "party": "VARCHAR",
    "district": "VARCHAR",
    **PROVENANCE_SCHEMA,
}
SPONSOR_COLUMNS = list(SPONSOR_SCHEMA)
COMMITTEE_MEMBER_SCHEMA = {
    "biennium": "VARCHAR",
    "committee_id": "VARCHAR",
    "committee_agency": "VARCHAR",
    "committee_name": "VARCHAR",
    "member_id": "VARCHAR",
    "name": "VARCHAR",
    "long_name": "VARCHAR",
    # The member's own identity fields, which the wire carries and the span tier
    # needs (#309): `is_person` screens name-blanked stubs on first+last name,
    # and `agency` is the member's chamber, distinct from `committee_agency`.
    "first_name": "VARCHAR",
    "last_name": "VARCHAR",
    "agency": "VARCHAR",
    "party": "VARCHAR",
    "district": "VARCHAR",
    **PROVENANCE_SCHEMA,
}
COMMITTEE_MEMBER_COLUMNS = list(COMMITTEE_MEMBER_SCHEMA)
MEETING_SCHEMA = {
    "meeting_window": "VARCHAR",
    "meeting_agency": "VARCHAR",
    "committee_id": "VARCHAR",
    "committee_agency": "VARCHAR",
    "committee_name": "VARCHAR",
    **PROVENANCE_SCHEMA,
}
MEETING_COLUMNS = list(MEETING_SCHEMA)
#: #600: when the ARCHIVE first held each member's listing for a biennium: ``fetched_at`` of
#: the earliest ok ``sponsors:<biennium>`` wire carrying them, and that wire's digest. Not when
#: WSL published it: for a biennium archived after the fact (all before 2027-28) it is just
#: the archive's start, so it is evidence only for one captured ahead of its term (#599).
FIRST_SEEN_SCHEMA = {
    "biennium": "VARCHAR",
    "member_id": "VARCHAR",
    "first_seen_at": "VARCHAR",
    "first_sha256": "VARCHAR",
    **PROVENANCE_SCHEMA,
}
FIRST_SEEN_COLUMNS = list(FIRST_SEEN_SCHEMA)


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
                    **_provenance(store, resource_id),
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
                    **_provenance(store, resource_id),
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
                    "first_name": record.get("FirstName"),
                    "last_name": record.get("LastName"),
                    "agency": record.get("Agency"),
                    "party": record.get("Party"),
                    "district": _text(record.get("District")),
                    **_provenance(store, resource_id),
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
                        **_provenance(store, resource_id),
                    }
                )
    return rows


def first_seen_rows(
    store: RawStore, *, parse: Parser = parsing.parse_sponsors
) -> list[dict[str, Any]]:
    """Every ``sponsors:<biennium>`` wire ever archived → each member's first ARCHIVED listing
    (#600) — an upper bound on when WSL listed them, never a lower one.

    Staging reads only the newest wire per resource; a member-elect's start is evidenced
    by the earliest, so this walks every run manifest. The nightly refetches an unchanged
    wire and records it again, so each distinct digest is parsed once, at its earliest
    fetch."""
    earliest = first_fetches(store, SPONSORS_RESOURCE_PREFIX)
    first: dict[tuple[str, str], tuple[str, str]] = {}  # (resource, member) → (at, sha)
    for (resource_id, sha), fetched_at in sorted(earliest.items()):
        wire = store.object_path(sha).read_bytes()
        for record in parse(wire):
            member = _text(record.get("Id"))
            if member is None:
                continue
            key = (resource_id, member)
            if key not in first or fetched_at < first[key][0]:
                first[key] = (fetched_at, sha)
    return [
        {
            "biennium": resource_id.removeprefix(SPONSORS_RESOURCE_PREFIX),
            "member_id": member,
            "first_seen_at": fetched_at,
            "first_sha256": sha,
            **_provenance(store, resource_id),
        }
        for (resource_id, member), (fetched_at, sha) in sorted(first.items())
    ]
