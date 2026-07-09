"""Synthesis — pure functions emitting canonical-row dicts for WSL anchors.

WSL has no ``GetBienniums`` / ``GetSessions`` endpoint, so the legislature
Organization, the chamber Orgs, the biennium-classified parent session, and
the regular sessions inside that biennium are *synthesized* (deterministic
parameter-driven values) rather than fetched. The result is consumed by
:mod:`usa_wa_adapter_legislature.bootstrap`, which upserts the rows.

Each function returns a plain ``dict`` whose keys match the SQLAlchemy column
names on :class:`Organization` / :class:`LegislativeSession`. No DB access,
no I/O — pure transforms of the inputs.

source / source_id conventions:

- All rows have ``source='usa_wa_legislature'``.
- ``source_id`` is the deterministic natural-key handle:
    - legislature → ``'usa_wa_legislature'``
    - chambers   → ``'usa_wa_house'`` / ``'usa_wa_senate'``
    - biennium   → ``'biennium:<biennium>'`` (e.g. ``'biennium:2025-26'``)
    - regular    → ``'session:<year>'`` (e.g. ``'session:2025'``)
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ulid import ULID as _ULID

_SOURCE = "usa_wa_legislature"
_BIENNIUM_RE = re.compile(r"^(\d{4})-(\d{2})$")

#: The two WA major-party Organizations synthesized for party Assignments (P1b).
#: Keyed by the **canonical party slug** — the bare value PM's ``org_wa_party``
#: identifier carries (power-map#270) and the sponsor normalizer canonicalizes
#: ``Party`` down to (``"R"``/``"Republican"`` → ``republican``). The ``source_id`` is
#: ``party-<slug>``; the descriptor derives the ``org_wa_party`` identifier value by
#: stripping that prefix. **No Independent party** — independent = the *absence* of a
#: party Assignment (power-map#270), so it is not an Org.
PARTY_ORG_NAMES: dict[str, str] = {
    "republican": "Washington State Republican Party",
    "democratic": "Washington State Democratic Party",
}


def parse_biennium(biennium: str) -> tuple[int, int]:
    """Parse a ``YYYY-YY`` biennium label into ``(start_year, end_year)``.

    ``2025-26`` → ``(2025, 2026)``. The end year is reconstructed from the
    start year's century, supporting decade rollovers (``2029-30``).
    """
    match = _BIENNIUM_RE.match(biennium)
    if match is None:
        raise ValueError(f"invalid biennium label: {biennium!r} (expected YYYY-YY)")
    start = int(match.group(1))
    end_suffix = int(match.group(2))
    century = (start // 100) * 100
    end = century + end_suffix
    if end < start:
        end += 100
    return start, end


def biennium_for_date(today: date) -> str:
    """Compute the WA biennium label (``YYYY-YY``) covering ``today``.

    Bienniums begin on odd years (2025-26, 2027-28, …). On an even year we
    roll back to the prior odd year.
    """
    start = today.year if today.year % 2 == 1 else today.year - 1
    end_suffix = (start + 1) % 100
    return f"{start}-{end_suffix:02d}"


def _biennium_start_year(label: str) -> int:
    """Parse the odd start year from a ``YYYY-YY`` biennium label."""
    return int(label.split("-", 1)[0])


def biennium_start_date(label: str) -> date:
    """The date a biennium begins — Jan 1 of its odd start year.

    WSL exposes no explicit committee name-change date; this biennium-start boundary
    is the documented approximation used to window a detected rename (#46).
    """
    return date(_biennium_start_year(label), 1, 1)


def previous_biennium(label: str) -> str:
    """The biennium two years before ``label`` (the rename diff's "before" side, #46)."""
    start = _biennium_start_year(label) - 2
    return f"{start}-{(start + 1) % 100:02d}"


def legislature_org(jurisdiction_id: _ULID) -> dict[str, Any]:
    """The Washington State Legislature Organization row."""
    return {
        "source": _SOURCE,
        "source_id": _SOURCE,
        "jurisdiction_id": jurisdiction_id,
        "name": "Washington State Legislature",
        "short_name": "WA Legislature",
        "org_type": "legislature",
        "parent_organization_id": None,
    }


def chamber_orgs(legislature_id: _ULID, jurisdiction_id: _ULID) -> list[dict[str, Any]]:
    """House and Senate chamber Organization rows (child of the legislature)."""
    return [
        {
            "source": _SOURCE,
            "source_id": "usa_wa_house",
            "jurisdiction_id": jurisdiction_id,
            "name": "Washington State House of Representatives",
            "short_name": "House",
            "org_type": "chamber",
            "parent_organization_id": legislature_id,
        },
        {
            "source": _SOURCE,
            "source_id": "usa_wa_senate",
            "jurisdiction_id": jurisdiction_id,
            "name": "Washington State Senate",
            "short_name": "Senate",
            "org_type": "chamber",
            "parent_organization_id": legislature_id,
        },
    ]


def party_orgs(jurisdiction_id: _ULID) -> list[dict[str, Any]]:
    """The two WA major-party Organization rows (``org_type='party'``).

    Parented to nothing (a party is not part of the legislative org tree; it is a
    jurisdiction-scoped political Org), keyed ``source_id='party-<slug>'``. PM already
    holds these two orgs backfilled with the ``org_wa_party`` identifier (power-map#270),
    so the org descriptor's identifier match auto-attaches ours. No Independent party
    (independent = absence of a party Assignment)."""
    return [
        {
            "source": _SOURCE,
            "source_id": f"party-{slug}",
            "jurisdiction_id": jurisdiction_id,
            "name": name,
            "short_name": None,
            "org_type": "party",
            "parent_organization_id": None,
        }
        for slug, name in PARTY_ORG_NAMES.items()
    ]


def biennium_session(legislature_id: _ULID, biennium: str) -> dict[str, Any]:
    """The biennium parent session row (``classification='biennium'``).

    Bills span regular and special sessions within a biennium, so the biennium
    itself is modeled as a parent session. Its child regular/special sessions
    reference it via ``parent_legislative_session_id``.
    """
    # Validate the biennium label up front so callers get a clear ValueError
    # before any DB write fans out from the synthesized row.
    parse_biennium(biennium)
    return {
        "source": _SOURCE,
        "source_id": f"biennium:{biennium}",
        "organization_id": legislature_id,
        "slug": f"usa-wa-{biennium}",
        "name": f"Washington State Legislature, {biennium} Biennium",
        "classification": "biennium",
        "biennium_label": biennium,
        "parent_legislative_session_id": None,
        "is_active": False,
    }


def regular_sessions(
    biennium_session_id: _ULID,
    legislature_id: _ULID,
    biennium: str,
) -> list[dict[str, Any]]:
    """Regular session rows — one per calendar year of the biennium."""
    start, end = parse_biennium(biennium)
    rows = []
    for year in (start, end):
        rows.append(
            {
                "source": _SOURCE,
                "source_id": f"session:{year}",
                "organization_id": legislature_id,
                "slug": f"usa-wa-{year}",
                "name": f"Washington State Legislature, {year} Regular Session",
                "classification": "regular",
                "biennium_label": biennium,
                "parent_legislative_session_id": biennium_session_id,
                "is_active": False,
            }
        )
    return rows
