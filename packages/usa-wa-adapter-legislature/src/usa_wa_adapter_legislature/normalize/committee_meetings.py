"""Committee-meeting normalizer — GetCommitteeMeetings → Joint/Other Organization rows.

`CommitteeService` is structurally blind to Joint/`Other` committees (#39); the only
programmatic source is each meeting's nested ``Committees.Committee[]``. This
normalizer flattens those refs across a window's meetings, dedups by the stable WSL
``Id``, and emits canonical :class:`Organization` rows for the **Joint/`Other` class
only**.

House/Senate committee refs ride along in the meeting data but are deliberately
**skipped** here: they are `CommitteeService`'s domain (cleaner, un-prefixed names;
``org_type='committee'``), and emitting them from this path would clobber those rows
on the shared ``(source, source_id)`` natural key with the agency-double-prefixed
``LongName`` and ``org_type='other'``.

Mappings (#39, validated against live 2023-24 / 2025-26 data):

- ``source_id = str(Id)`` — negative sentinels (JTC ``-140``, JLARC ``-5`` …) are
  stable across bienniums, so they are valid natural keys.
- ``name = LongName`` **verbatim** — agency-double-prefixed ("Joint Joint …"); no
  cleaning here, PM curates display downstream.
- ``short_name = Name``; ``org_type = "other"`` for the whole class.
- parent = the WA Legislature anchor (Joint and Other alike) via
  :func:`~usa_wa_adapter_legislature.normalize.committees.parent_for_agency`.

Window-absence is **not** retirement for this class (dormancy is normal): this
normalizer only ever emits/updates the bodies present in the window — it never
marks an absent body inactive.
"""

from __future__ import annotations

from typing import Any

from ulid import ULID as _ULID

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.bootstrap import BootstrapAnchors
from usa_wa_adapter_legislature.normalize.committees import parent_for_agency
from usa_wa_adapter_legislature.normalize.fields import clean_field

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
#: The agencies this path owns — the bodies CommitteeService cannot see.
_MEETING_DERIVED_AGENCIES = frozenset({"Joint", "Other"})


def _committee_refs(meeting: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the nested ``Committees.Committee[]`` off one meeting dict.

    zeep renders a single child as a dict and multiple as a list; a meeting with no
    committee block yields ``[]``."""
    block = meeting.get("Committees") or {}
    coms = block.get("Committee") if isinstance(block, dict) else None
    if coms is None:
        return []
    return [coms] if isinstance(coms, dict) else list(coms)


def joint_other_refs(meetings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Deduped Joint/`Other` committee refs across a window's meetings, keyed by source_id.

    Flattens every meeting's nested ``Committees.Committee[]``, keeps only the
    Joint/`Other` class (the bodies ``CommitteeService`` can't see, #39), and dedups by
    the stable WSL ``Id`` (``first ref wins`` — a body repeats once per meeting it held).
    House/Senate refs ride along in the meeting data but are dropped: they are
    ``CommitteeService``'s domain.

    The shared parse seam for both consumers — :func:`normalize_committee_meetings` (the
    canonical row producer) and the #56 rename detector's meeting-cohort builder — so the
    dedup/filter rule lives in exactly one place. Returns raw refs (``Id, Name, LongName,
    Agency, Acronym, Phone``); each consumer extracts and guards the fields it needs.

    Dedup is **structural** (first ref for an ``Id`` wins, regardless of field completeness) —
    field-presence is the consumer's concern, not this seam's. In WSL data a body's refs carry
    identical attributes across the meetings it held, so first-wins is unambiguous; a malformed
    first ref shadowing a later complete one is a theoretical edge that has never been observed
    (every produced body carries a populated ``Name``/``LongName``)."""
    by_id: dict[str, dict[str, Any]] = {}
    for meeting in meetings:
        for ref in _committee_refs(meeting):
            if ref.get("Agency") not in _MEETING_DERIVED_AGENCIES:
                continue  # House/Senate belong to CommitteeService — see module docstring
            committee_id = ref.get("Id")
            if committee_id is None:
                continue
            source_id = str(committee_id)
            if source_id not in by_id:  # refs repeat once per meeting the body held; first wins
                by_id[source_id] = ref
    return by_id


async def normalize_committee_meetings(
    payload: FetchedPayload,
    *,
    anchors: BootstrapAnchors,
    jurisdiction_id: _ULID,
) -> NormalizedBatch:
    """Emit deduped Joint/`Other` Organization rows from a meeting-window payload.

    Reads ``payload.parsed`` (the zeep-derived meeting dicts the adapter carries
    alongside the archived SOAP wire). The raw ``body`` is XML, not JSON, so there is
    no JSON fallback — an unparsed payload yields an empty batch with a warning."""
    meetings = payload.parsed
    if meetings is None:
        logger.warning("wsl_meetings_payload_unparsed", extra={"url": payload.url})
        return NormalizedBatch()

    entities: list[Organization] = []
    for source_id, ref in joint_other_refs(meetings).items():
        long_name = ref.get("LongName")
        if not long_name:
            logger.warning(
                "wsl_meeting_committee_missing_longname",
                extra={"committee_id": ref.get("Id"), "agency": ref.get("Agency")},
            )
            continue

        acronym = clean_field(ref.get("Acronym"))
        entities.append(
            Organization(
                source=_SOURCE,
                source_id=source_id,
                jurisdiction_id=jurisdiction_id,
                name=long_name,
                short_name=clean_field(ref.get("Name")),
                org_type="other",
                parent_organization_id=parent_for_agency(ref["Agency"], anchors),
                acronym=acronym.upper() if acronym else None,
                phone=clean_field(ref.get("Phone")),
            )
        )

    return NormalizedBatch(entities=entities)
