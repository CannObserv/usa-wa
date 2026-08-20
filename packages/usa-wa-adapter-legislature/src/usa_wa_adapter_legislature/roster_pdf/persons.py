"""Roster Person minting (#228 Phase B) — the write side of identity resolution.

Creates one :class:`~clearinghouse_domain_legislative.identity.Person` per **minted**
identity, keyed ``(usa_wa_legislature_roster, <identity key>)``. A WSL-joined identity
mints nothing — its Person already exists in the WSL source space, and a roster twin would
be exactly the duplicate the §2 join exists to prevent.

**The display name is the most recent listing's printed form**, with the position suffix
stripped (``– 19B`` is seat metadata, not a name) and whitespace collapsed. Recency
naturally prefers the modern form where the source's own style drifted — Margaret Hurley's
1970s listings over her 1950s marital form. Name *parts* stay ``None``: the roster prints
one string, and decomposing it would be inference the source doesn't attest. PM curates
display names on match, so this is a low-stakes local hint (the sponsor minting's
precedent).

Idempotent by natural key; a rebuild from the archive plus the adjudication tables
reproduces the same rows — the property the identity design's §1 re-derivability argument
depends on.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Person
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.identity import (
    _POSITION_SUFFIX,
    IDENTITY_MINTED,
    RosterIdentity,
)

logger = get_logger(__name__)


def display_name(identity: RosterIdentity) -> str:
    """The most recent listing's name, seat suffix stripped, whitespace collapsed."""
    latest = max(identity.records, key=lambda r: (r.year, r.order))
    return " ".join(_POSITION_SUFFIX.sub("", latest.name).split())


async def mint_roster_persons(
    session: AsyncSession, identities: Iterable[RosterIdentity]
) -> dict[str, int]:
    """Get-or-create a roster Person per minted identity; return ``{created, existing}``."""
    created = existing = 0
    for identity in identities:
        if identity.disposition != IDENTITY_MINTED:
            continue
        if identity.key is None:  # pragma: no cover - invalid by construction
            raise ValueError(f"minted identity {identity.fold!r} has no key")
        found = (
            await session.execute(
                select(Person).where(
                    Person.source == ROSTER_SOURCE_SLUG, Person.source_id == identity.key
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            existing += 1
            continue
        session.add(
            Person(
                source=ROSTER_SOURCE_SLUG,
                source_id=identity.key,
                name_full=display_name(identity),
            )
        )
        created += 1
    await session.flush()
    # ``created`` is a reserved LogRecord attribute — prefix the extras.
    logger.info(
        "roster_persons_minted",
        extra={"persons_created": created, "persons_existing": existing},
    )
    return {"created": created, "existing": existing}
