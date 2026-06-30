"""Committee normalizer — WSL CommitteeService dicts → canonical Organization rows.

Maps the WSDL ``Committee`` shape (``Id``, ``Name``, ``LongName``, ``Agency``,
``Acronym``, ``Phone``) onto :class:`Organization`, using the bootstrap anchors
to resolve the parent Org by ``Agency`` text (House/Senate → chamber; Joint →
the WA Legislature anchor).
"""

from __future__ import annotations

import json

from ulid import ULID as _ULID

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.fields import clean_field

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"


def parent_for_agency(agency: str | None, anchors: BootstrapAnchors) -> _ULID | None:
    """Resolve ``Agency`` to its parent Org id.

    'House'/'Senate' parent to the matching chamber. 'Joint' (cross-chamber bodies
    like Joint Transportation) and 'Other' (legislative agencies — LEAP, Statute Law
    Committee — surfaced only via the meeting docket, #39) parent to the WA Legislature
    anchor — their natural common ancestor — not to either chamber and not to NULL. Any
    other value is genuinely unknown → ``None`` (caller warns).

    Shared with the meeting-derived normalizer (:mod:`normalize.committee_meetings`),
    which only ever passes 'Joint'/'Other'."""
    if agency == "House":
        return anchors.house_id
    if agency == "Senate":
        return anchors.senate_id
    if agency in ("Joint", "Other"):
        return anchors.legislature_id
    return None


async def normalize_committees(
    payload: FetchedPayload,
    *,
    anchors: BootstrapAnchors,
    jurisdiction_id: _ULID,
) -> NormalizedBatch:
    """Parse a committees payload and emit canonical Organization rows.

    Prefers ``payload.parsed`` (the zeep-derived dicts the adapter carries
    alongside the archived SOAP wire, #54); falls back to decoding ``body`` as
    JSON for the pre-archival payload shape (and JSON-body tests).
    """
    if payload.parsed is not None:
        committees = payload.parsed
    else:
        committees = json.loads(payload.body.decode("utf-8"))
    entities: list[Organization] = []
    for committee in committees:
        long_name = committee.get("LongName")
        if not long_name:
            logger.warning(
                "wsl_committee_missing_longname",
                extra={
                    "committee_id": committee.get("Id"),
                    "agency": committee.get("Agency"),
                },
            )
            continue

        agency = committee.get("Agency")
        parent_id = parent_for_agency(agency, anchors)
        if parent_id is None:
            logger.warning(
                "wsl_committee_unknown_agency",
                extra={
                    "committee_id": committee.get("Id"),
                    "agency": agency,
                },
            )

        # clean_field collapses ""/"   "/non-str to None, so a blank Acronym/Phone
        # becomes a single "absent" value rather than "" (shared with the meeting
        # normalizer — see normalize/fields.py).
        acronym = clean_field(committee.get("Acronym"))

        entities.append(
            Organization(
                source=_SOURCE,
                source_id=str(committee["Id"]),
                jurisdiction_id=jurisdiction_id,
                name=long_name,
                short_name=clean_field(committee.get("Name")),
                org_type="committee",
                parent_organization_id=parent_id,
                acronym=acronym.upper() if acronym else None,
                phone=clean_field(committee.get("Phone")),
            )
        )

    return NormalizedBatch(entities=entities)
