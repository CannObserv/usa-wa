"""Two published entities, one human (#378 step 4).

`persons` is seeded from namespaces that mint independently — the legislature's
numeric member id, the roster PDF's ``<fold>:<year>``, the PDC's filer id — so a
legislator present in two of them is published twice, with nothing in
`person_crosswalk` saying the ids are one person. Seventeen such pairs reached
power-map, which resolved each member-id entity to a live person and planned each
roster twin as a **create**: Patty Murray, Jack Metcalf and Ellen Craswell were
one applier run from being duplicated downstream. The merges closed those; this
is what makes the eighteenth a build failure instead of a consumer's discovery.

**Grouping is :func:`identity_fold`, called and not restated.** It is the same
fold the roster↔WSL matcher joins on, so a gate keyed on a private copy would
drift away from the thing it exists to backstop — the second implementation CR
155 consolidated out of `usa_wa_common.names`, arriving by a different door.

**What it does not catch.** The fold keeps middle initials, so `Stanley Johnson`
/ `Stanley C. Johnson` and `Gerald "Jerry" Saling` / `Gerald L. "Jerry" Saling`
— 2 of the 17 — are invisible here, as is `Mike Kreidler` against the
`Myron "Mike" Kreidler` the roster prints. 14 of the 17, not 17. The loosening
that would recover the two initials cases — first given token plus surname —
collides **42 groups over 88 rows** on the live corpus, among them
`N. B. Atkinson` / `N. P. Atkinson`, which :mod:`roster_pdf.identity` documents
as distinct on five independent grounds, and `William Bishop, Jr.` against
`William H. Price, Jr` on ``jr`` as the surname. Three defensible allowlist
entries against forty-two judgment calls to recover two of seventeen: the miss
is the bought half of a trade, and the two go to hand review instead.

**The allowlist is an adjudication, and reads what it can.** A roster split
mints two entities from one fold *on purpose*, so every :data:`IDENTITY_SPLITS`
key is a published collision by construction — derived rather than copied, or
the next split added there would break the nightly until someone found the
second file. What is left is :data:`DISTINCT_NAMESAKES`, whose entries carry
their evidence because allowlisting a fold tells the build to stop looking at it
forever, and an unexplained entry is indistinguishable from a duplicate someone
waved through.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from usa_wa_adapter_legislature.roster_pdf.identity import IDENTITY_SPLITS, identity_fold

#: The work order's shape: the grouping key first, then the row it names, so the
#: table sorts into the pairs a reviewer adjudicates.
COLLISION_SCHEMA = {
    "name_fold": "VARCHAR",
    "entity_id": "VARCHAR",
    "name_full": "VARCHAR",
    "name_source": "VARCHAR",
}
COLLISION_COLUMNS = list(COLLISION_SCHEMA)

#: Folds published under two LIVE entities that are two different people, with
#: the corpus evidence that settles each. Not roster splits — those derive from
#: :data:`IDENTITY_SPLITS`; these are WSL-internal, two member ids apiece.
DISTINCT_NAMESAKES: dict[str, str] = {
    # Senate LD4 1981-2010 (WSL 268, also the roster's bobmccaslin:1981) against
    # House LD4 2015-2022 (WSL 20741, also wa_pdc:147). Disjoint in time,
    # different chamber, a campaign filing on the junior only: father and son.
    "bobmccaslin": "WSL 268 Senate LD4 1981-2010 vs WSL 20741 House LD4 2015-2022; father and son",
    # Both WSL-keyed with no roster key, so both publish the bare name the
    # sponsor index prints. The roster keeps them apart: Brian JOSEPH Sullivan,
    # LD29 House 1997-2000 (WSL 2132), and Brian JAMES Sullivan, LD21 House
    # 2001-2008 (WSL 7240, "Resigned Jan. 5, 2008; Elected to the Snohomish
    # County Council"). Different middle names, districts and counties.
    "briansullivan": "WSL 2132 Brian Joseph LD29 1997-2000 vs WSL 7240 Brian James LD21 2001-08",
}


def allowed_folds() -> frozenset[str]:
    """Every fold two live entities may share: the adjudicated splits plus the namesakes."""
    return frozenset(DISTINCT_NAMESAKES) | frozenset(IDENTITY_SPLITS)


def _fold(name: Any) -> str:
    """The row's grouping key, or ``''`` when it has none.

    Empty is NOT a key. A null name is the #366 acceptance — an entity no source
    attests — and an all-annotation name folds empty too; grouping on that would
    read every unnamed entity in the corpus as one person.
    """
    if not isinstance(name, str):
        return ""
    return identity_fold(name)


def collision_rows(persons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every row of every un-allowlisted fold shared by two or more live entities.

    Both sides are emitted, not just a presumed loser: which id survives a merge
    is an adjudication (the 17 went roster → member-id because power-map had
    already resolved that side), and a gate that named one side would be
    presuming the answer it exists to ask for.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for person in persons:
        fold = _fold(person["name_full"])
        if not fold:
            continue
        grouped[fold].append(
            {
                "name_fold": fold,
                "entity_id": person["entity_id"],
                "name_full": person["name_full"],
                "name_source": person.get("name_source"),
            }
        )
    allowed = allowed_folds()
    return [
        row
        for fold, rows in sorted(grouped.items())
        if len(rows) > 1 and fold not in allowed
        for row in rows
    ]
