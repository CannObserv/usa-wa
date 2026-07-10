"""Sponsor normalizer — WSL ``SponsorService.GetSponsors`` → Person + identifier.

Emits, per **named** member row (step 0's ``is_person`` filters the name-blanked stubs):

- a :class:`Person` (``source_id`` = the stable WSL member ``Id``) + a
  :class:`PersonIdentifier` (the ``wa_legislature_member_id`` scheme).

**Persons only (#78 increment 2c).** Party + Senate-seat tenure are no longer emitted
per-biennium here — they are **merged spans** built from the full sponsor archive by the
span engine (:mod:`usa_wa_adapter_legislature.harvest_sponsor_spans`), which the daily
refresh re-drives for the current biennium after archiving ``sponsors:<current>``. This
normalizer's sole job is materializing the Person cluster the spans resolve against; the
per-biennium ``_emit_party``/``_emit_chamber`` inline emission it used to carry is retired
(a per-biennium row per member per dimension became one span with a real
``valid_from..valid_to``). See the #78 design spec, § "Subsuming the current path".

Iterates **rows, not members** (a mid-biennium House→Senate mover has two named rows
under one ``Id``); :func:`get_or_create_person` and the :class:`EntityCollector` dedup by
identity so the Person collapses to one. See :mod:`normalize.members` for why the
get-or-create helper touches the session (intra-batch FK resolution the runner can't do).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.adapter import FetchedPayload, NormalizedBatch
from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.normalize.members import normalize_member_persons

logger = get_logger(__name__)


async def normalize_sponsors(
    payload: FetchedPayload,
    *,
    session: AsyncSession,
) -> NormalizedBatch:
    """Parse a sponsors payload and emit the Person cluster (Person + identifier only).

    ``session`` is the runner's session (same transaction); :func:`get_or_create_person`
    resolves the Person id so the identifier carries a real FK. Party/Senate-seat
    Assignments are **not** emitted here — they are archive-derived merged spans (#78,
    Phase B). Persons dedup across biennia by the stable WSL ``Id`` (#81), so a member seen
    in many biennia collapses to one Person.

    Delegates to :func:`~usa_wa_adapter_legislature.normalize.members.normalize_member_persons`
    — the shared "member rows → Person cluster" projection, also used by the #82 historical
    committee roster (``GetCommitteeMembers`` returns the same flat ``Member`` shape)."""
    return await normalize_member_persons(payload, session=session)
