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
depends on. *Rows*, not just keys: a re-run whose derived display name differs (a parse
fix corrected a printed form) **updates** the existing Person rather than leaving the
stale string in place.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Person
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_MINTED,
    RosterIdentity,
    strip_position_suffix,
)

logger = get_logger(__name__)


def display_name(identity: RosterIdentity) -> str:
    """The most recent listing's name, seat suffix stripped, whitespace collapsed."""
    latest = max(identity.records, key=lambda r: (r.year, r.order))
    return " ".join(strip_position_suffix(latest.name).split())


async def mint_roster_persons(
    session: AsyncSession, identities: Iterable[RosterIdentity]
) -> dict[str, int]:
    """Upsert a roster Person per minted identity; return ``{created, existing, renamed}``.

    The existing rows are loaded in **one** query keyed by ``source_id`` (CR #92 — the
    per-identity SELECT was ~2,500 round trips on the prod corpus), and an existing row
    whose ``name_full`` no longer matches the derived display name is **refreshed**
    (CR #89): a parse fix that corrects a printed form has to reach the database, or the
    rows are re-derivable in key but not in content.
    """
    minted = []
    for identity in identities:
        if identity.disposition != IDENTITY_MINTED:
            continue
        if identity.key is None:  # pragma: no cover - invalid by construction
            raise ValueError(f"minted identity {identity.fold!r} has no key")
        minted.append(identity)

    keys = [i.key for i in minted]
    found = {
        person.source_id: person
        for person in (
            (
                await session.execute(
                    select(Person).where(
                        Person.source == ROSTER_SOURCE_SLUG, Person.source_id.in_(keys)
                    )
                )
            )
            .scalars()
            .all()
        )
    }

    created = existing = renamed = 0
    for identity in minted:
        name = display_name(identity)
        person = found.get(identity.key)
        if person is not None:
            existing += 1
            if person.name_full != name:
                person.name_full = name
                renamed += 1
            continue
        session.add(Person(source=ROSTER_SOURCE_SLUG, source_id=identity.key, name_full=name))
        created += 1
    await session.flush()
    # ``created`` is a reserved LogRecord attribute — prefix the extras.
    logger.info(
        "roster_persons_minted",
        extra={
            "persons_created": created,
            "persons_existing": existing,
            "persons_renamed": renamed,
        },
    )
    return {"created": created, "existing": existing, "renamed": renamed}
