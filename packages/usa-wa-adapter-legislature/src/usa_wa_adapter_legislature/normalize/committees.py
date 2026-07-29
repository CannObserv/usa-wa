"""Committee normalizer — WSL CommitteeService dicts → canonical Organization rows.

Maps the WSDL ``Committee`` shape (``Id``, ``Name``, ``LongName``, ``Agency``,
``Acronym``, ``Phone``) onto :class:`Organization`, using the bootstrap anchors
to resolve the parent Org by ``Agency`` text (House/Senate → chamber; Joint →
the WA Legislature anchor).
"""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID as _ULID

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.fields import clean_field

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"

_SUBCOMMITTEE_ON = " subcommittee on "
_SUBCOMMITTEE_TO = " subcommittee to "


def _fold(name: str) -> str:
    """Case/punctuation-insensitive key for matching a subcommittee's inferred parent
    name to a concurrent committee in the same wire. Folds ``&`` to ``and`` so
    'Health & Long Term Care' and 'Health and Long Term Care' key alike."""
    lowered = name.lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", lowered).split())


def infer_subcommittee_parent_name(short_name: str | None) -> str | None:
    """Infer a subcommittee's parent committee name from its short ``Name``.

    Handles both WSL shapes: ``"{Parent} Subcommittee on {X}"`` (parent leads, e.g.
    'Appropriations Subcommittee on Education' → 'Appropriations') and ``"{X}
    Subcommittee to {Parent}"`` (parent trails, e.g. 'Behavioral Health Subcommittee to
    Health & Long Term Care' → 'Health & Long Term Care').

    Returns ``None`` when the ``"Subcommittee"`` token is absent. **WSL is inconsistent**
    (usa-wa#124): the same subcommittee ``Id`` carries the token in some biennia and a
    bare form in others (12174 was 'Appropriations Subcommittee on Education' in 2007-08
    but 'Education Appropriations' in 2009-10). The bare form is undetectable per-record —
    accepted, because every sub carries the token at its **creation** biennium and the
    daily refresh is ``fill_only`` (it won't rewrite an existing row's parent), so a
    correct parent set once at creation persists."""
    if not short_name:
        return None
    lowered = short_name.lower()
    idx = lowered.find(_SUBCOMMITTEE_ON)
    if idx != -1:
        return short_name[:idx].strip() or None
    idx = lowered.find(_SUBCOMMITTEE_TO)
    if idx != -1:
        return short_name[idx + len(_SUBCOMMITTEE_TO) :].strip() or None
    return None


async def _resolve_subcommittee_parent(
    session: AsyncSession | None,
    agency: str | None,
    parent_name: str,
    batch_by_name: dict[tuple[str | None, str], str],
) -> _ULID | None:
    """Resolve a subcommittee's parent committee to its persisted Organization id.

    Scoped to the **same wire batch** (same ``Agency`` + folded name) so the match is a
    concurrent committee — names drift across biennia, so a bare cross-biennium name
    lookup would mis-resolve. The parent committee always predates its subcommittees in
    real WA data (Appropriations since 1991; its subs from 2007), so it is already
    persisted; ``None`` (→ chamber fallback) when there is no session, the parent isn't
    in this wire, or it isn't persisted yet (a would-be first-ingest edge, #124)."""
    if session is None:
        return None
    parent_source_id = batch_by_name.get((agency, _fold(parent_name)))
    if parent_source_id is None:
        return None
    row = await session.scalar(
        select(Organization).where(
            Organization.source == _SOURCE,
            Organization.source_id == parent_source_id,
            Organization.org_type == "committee",
        )
    )
    return row.id if row is not None else None


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
    session: AsyncSession | None = None,
) -> NormalizedBatch:
    """Parse a committees payload and emit canonical Organization rows.

    Prefers ``payload.parsed`` (the zeep-derived dicts the adapter carries
    alongside the archived SOAP wire, #54); falls back to decoding ``body`` as
    JSON for the pre-archival payload shape (and JSON-body tests).

    ``session`` (optional) enables **subcommittee parenting** (usa-wa#124): a
    subcommittee parents to its parent *committee* (resolved from the same wire) rather
    than the chamber. Without a session, or when the parent can't be resolved, the
    chamber fallback (historical behavior) applies.
    """
    if payload.parsed is not None:
        committees = payload.parsed
    else:
        committees = json.loads(payload.body.decode("utf-8"))
    # Index the wire by (Agency, folded short-name) → source_id so a subcommittee can
    # find its parent committee among its concurrent siblings (#124).
    batch_by_name: dict[tuple[str | None, str], str] = {}
    for c in committees:
        short = clean_field(c.get("Name"))
        if short and c.get("Id") is not None:
            batch_by_name[(c.get("Agency"), _fold(short))] = str(c["Id"])
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
        short_name = clean_field(committee.get("Name"))

        # Subcommittee → parent to its parent committee (not the chamber), #124. Falls
        # back to the chamber when the parent can't be resolved (no session / parent
        # absent from this wire / not-yet-persisted), preserving historical behavior.
        parent_name = infer_subcommittee_parent_name(short_name)
        if parent_name is not None:
            resolved = await _resolve_subcommittee_parent(
                session, agency, parent_name, batch_by_name
            )
            if resolved is not None:
                parent_id = resolved
            else:
                logger.warning(
                    "wsl_subcommittee_parent_unresolved",
                    extra={
                        "committee_id": committee.get("Id"),
                        "agency": agency,
                        "inferred_parent": parent_name,
                    },
                )

        entities.append(
            Organization(
                source=_SOURCE,
                source_id=str(committee["Id"]),
                jurisdiction_id=jurisdiction_id,
                name=long_name,
                short_name=short_name,
                org_type="committee",
                parent_organization_id=parent_id,
                acronym=acronym.upper() if acronym else None,
                phone=clean_field(committee.get("Phone")),
            )
        )

    return NormalizedBatch(entities=entities)
