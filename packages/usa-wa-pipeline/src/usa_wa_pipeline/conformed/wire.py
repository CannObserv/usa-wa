"""Staging rows → the wire shapes the imported projectors consume (#309).

Every span family reuses the Postgres tier's own projectors, and those read the
upstream wire's field names. Staging carries the same facts under normalized
names, so one adapter restores the shape — re-labelling, never re-interpreting
(the AGENTS.md rule: never key a parser on an exact upstream string; these
functions key on *staging's* names and hand the upstream ones back untouched).

Its own module because both :mod:`usa_wa_pipeline.conformed.spans` and
:mod:`usa_wa_pipeline.conformed.house` need it, and the House family is built
from inside the sponsor build (as #267 context) — so the two would otherwise
import each other.
"""

from __future__ import annotations

from typing import Any


def wsl_wire(row: dict[str, Any]) -> dict[str, Any]:
    """One staging row in the WSL SOAP wire's own shape, for the projectors."""
    return {
        "Id": row.get("member_id"),
        "FirstName": row.get("first_name"),
        "LastName": row.get("last_name"),
        "Name": row.get("name"),
        "LongName": row.get("long_name"),
        "Agency": row.get("agency"),
        "Party": row.get("party"),
        "District": row.get("district"),
    }


def sponsor_wire_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Staging sponsor rows → ``{biennium: [wire dicts]}`` (the roster-map shape)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        biennium = row.get("biennium")
        if not biennium:
            continue
        out.setdefault(str(biennium), []).append(wsl_wire(row))
    return out


def committee_rosters(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Staging committee-member rows → ``{(biennium, committee_id): [wire dicts]}``."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        biennium, committee_id = row.get("biennium"), row.get("committee_id")
        if not biennium or not committee_id:
            continue
        out.setdefault((str(biennium), str(committee_id)), []).append(wsl_wire(row))
    return out
