"""Committee normalizer — WSL CommitteeService dicts → canonical Organization rows.

Maps the WSDL ``Committee`` shape (``Id``, ``Name``, ``LongName``, ``Agency``,
``Acronym``, ``Phone``) onto :class:`Organization`, using the bootstrap anchors
to resolve the parent chamber Org by ``Agency`` text.
"""

from __future__ import annotations

import json

from ulid import ULID as _ULID

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"


def _chamber_parent_for(agency: str | None, anchors: BootstrapAnchors) -> _ULID | None:
    """Resolve ``Agency`` ('House' / 'Senate') to the matching chamber Org id."""
    if agency == "House":
        return anchors.house_id
    if agency == "Senate":
        return anchors.senate_id
    return None


async def normalize_committees(
    payload: FetchedPayload,
    *,
    anchors: BootstrapAnchors,
    jurisdiction_id: _ULID,
) -> NormalizedBatch:
    """Parse a committees payload and emit canonical Organization rows."""
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
        parent_id = _chamber_parent_for(agency, anchors)
        if parent_id is None:
            logger.warning(
                "wsl_committee_unknown_agency",
                extra={
                    "committee_id": committee.get("Id"),
                    "agency": agency,
                },
            )

        acronym = committee.get("Acronym")
        phone_raw = committee.get("Phone")
        phone = phone_raw.strip() if isinstance(phone_raw, str) else None
        # Whitespace-only WSL Phone values collapse to ``""`` after strip; treat
        # those as missing so downstream readers don't see two truth values
        # ("" vs None) for "no phone."
        if phone == "":
            phone = None

        entities.append(
            Organization(
                source=_SOURCE,
                source_id=str(committee["Id"]),
                jurisdiction_id=jurisdiction_id,
                name=long_name,
                short_name=committee.get("Name"),
                org_type="committee",
                parent_organization_id=parent_id,
                acronym=acronym.upper() if isinstance(acronym, str) else None,
                phone=phone,
            )
        )

    return NormalizedBatch(entities=entities)
