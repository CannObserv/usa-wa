"""Conformed committee lineage (#447): the operator-attested succession links, published.

usa-wa holds the links in ``registry.committee_succession_events`` (#124): which
era-``Id`` of a committee continued (``succeeded_by``), split from (``split_from``)
or merged with (``merged_with``) which. The C3 producer that pushed them to
power-map retired with the sync (#314), so until this dataset every link recorded
since reached nobody — Civic Health's ``35341 → 36500`` (#444) among them.

**One row per current link, ends resolved through the crosswalk.** Each WSL id
resolves to its entity through the shared merge walk (:mod:`.crosswalk`, #366),
so an edge publishes on the entity a consumer can still resolve; the raw ids ride
beside it. An end that resolves to nothing publishes NULL rather than dropping
the row: a dropped link is a silent retraction, while the NULL fails the build's
``not_null`` gate and leaves the table as the hand-review work order.

**INV2 lives here as data** (:func:`retired_entities`): the subject of a
``succeeded_by`` or ``merged_with`` link was succeeded, so ``organizations``
derives it inactive; ``split_from`` leaves a split child live. The
``organizations_succeeded_are_inactive`` test gates the wiring.

**Cycles are legitimate; untimely ones are not** (:func:`untimely_cycles`). WSL
re-uses committee ids, so a round-trip rename is a real cycle — five are in
production, e.g. ``924 → 966 → 924`` (1993/1995), and #126 records them as data.
"No cycles" would refuse the nightly over correct history. What no history can
produce is a cycle that cannot be read in time order: going around once, the
years climb and wrap back exactly once. A same-year reversal (both ``A → B`` and
``B → A`` in 2001) wraps twice — one of the two is a direction error — and an
undated link on a cycle cannot be ordered at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from numbers import Integral
from typing import Any

from clearinghouse_domain_legislative.committee_succession import (
    SLUG_MERGED_WITH,
    SLUG_SUCCEEDED_BY,
)
from usa_wa_pipeline.conformed.crosswalk import merge_map, resolve_merged

LINEAGE_SCHEMA = {
    "subject_entity_id": "VARCHAR",
    "slug": "VARCHAR",
    "linked_entity_id": "VARCHAR",
    "subject_source_id": "VARCHAR",
    "linked_source_id": "VARCHAR",
    "effective_year": "BIGINT",
    "evidence_url": "VARCHAR",
    "notes": "VARCHAR",
}
LINEAGE_COLUMNS = list(LINEAGE_SCHEMA)

CYCLE_SCHEMA = {
    "source_ids": "VARCHAR",
    "entity_ids": "VARCHAR",
    "effective_years": "VARCHAR",
}
CYCLE_COLUMNS = list(CYCLE_SCHEMA)

#: The slugs whose subject is a predecessor that ended — INV2's scope, and the
#: forward-flow (predecessor → successor) edges the cycle gate walks.
RETIRING_SLUGS = frozenset({SLUG_SUCCEEDED_BY, SLUG_MERGED_WITH})

#: The namespace committee ids are registered under (``registrar.load_org_keys``).
_COMMITTEE_NAMESPACE = "usa_wa_legislature"

_ARROW = " → "


def _committee_index(crosswalk: list[dict[str, Any]]) -> dict[str, str]:
    """WSL committee id → its live entity, merge tombstones followed."""
    merges = merge_map(crosswalk)
    return {
        str(row["key_value"]): resolve_merged(merges, str(row["entity_id"]))
        for row in crosswalk
        if row["key_namespace"] == _COMMITTEE_NAMESPACE
    }


def lineage_rows(crosswalk: list[dict[str, Any]], links: Iterable[Any]) -> list[dict[str, Any]]:
    """One published row per current succession link (``SuccessionRow``-shaped)."""
    index = _committee_index(crosswalk)
    return [
        {
            "subject_entity_id": index.get(link.subject_source_id),
            "slug": link.slug,
            "linked_entity_id": index.get(link.linked_source_id),
            "subject_source_id": link.subject_source_id,
            "linked_source_id": link.linked_source_id,
            "effective_year": link.effective_year,
            "evidence_url": link.evidence_url,
            "notes": link.notes,
        }
        for link in links
    ]


def retired_entities(lineage: Iterable[dict[str, Any]]) -> set[str]:
    """Entities a ``succeeded_by`` / ``merged_with`` link names as the predecessor."""
    return {
        row["subject_entity_id"]
        for row in lineage
        if row["slug"] in RETIRING_SLUGS and isinstance(row["subject_entity_id"], str)
    }


def _year(value: Any) -> int | None:
    """An ``effective_year`` as an int, or ``None`` for undated.

    The cycle model reads ``org_lineage`` back through ``.df()``: an INTEGER
    column holding a NULL arrives as float64 (``1993.0``, ``NaN``), one without
    as numpy ints. Reading a float year as undated would refuse every
    legitimate round trip the night one link lost its year.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _time_ordered(years: list[int | None]) -> bool:
    """Whether a cycle's years, read around it once, climb and wrap exactly once."""
    if any(year is None for year in years):
        return False
    wraps = sum(1 for i, year in enumerate(years) if years[(i + 1) % len(years)] <= year)
    return wraps == 1


def untimely_cycles(lineage: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every simple cycle of forward-flow edges that cannot be read in time order.

    Enumerates simple cycles rooted at their smallest entity (each found once),
    walking only nodes above the root; the on-path set bounds every walk, so it
    terminates on any graph (the cycle guard ``CommitteeSuccessionEvent`` asks of
    a walker). Parallel edges are distinct cycles, each checked on its own.
    Self-loops and NULL ends are skipped — each has its own gate.
    """
    edges: dict[str, list[dict[str, Any]]] = {}
    for row in lineage:
        src, dst = row["subject_entity_id"], row["linked_entity_id"]
        if row["slug"] not in RETIRING_SLUGS or not isinstance(src, str):
            continue
        if not isinstance(dst, str) or src == dst:
            continue
        edges.setdefault(src, []).append(row)
    for out in edges.values():
        out.sort(key=lambda r: (r["linked_entity_id"], _year(r["effective_year"]) or 0, r["slug"]))

    found: list[dict[str, Any]] = []

    def _walk(root: str, node: str, path: list[dict[str, Any]], on_path: set[str]) -> None:
        for edge in edges.get(node, ()):
            nxt = edge["linked_entity_id"]
            if nxt == root:
                cycle = [*path, edge]
                years = [_year(e["effective_year"]) for e in cycle]
                if not _time_ordered(years):
                    found.append(_cycle_row(cycle, years))
            elif nxt > root and nxt not in on_path:
                on_path.add(nxt)
                _walk(root, nxt, [*path, edge], on_path)
                on_path.discard(nxt)

    for root in sorted(edges):
        _walk(root, root, [], {root})
    return found


def _cycle_row(cycle: list[dict[str, Any]], years: list[int | None]) -> dict[str, Any]:
    return {
        "source_ids": _ARROW.join(
            [*(e["subject_source_id"] for e in cycle), cycle[-1]["linked_source_id"]]
        ),
        "entity_ids": _ARROW.join(
            [*(e["subject_entity_id"] for e in cycle), cycle[-1]["linked_entity_id"]]
        ),
        "effective_years": _ARROW.join("null" if y is None else str(y) for y in years),
    }
