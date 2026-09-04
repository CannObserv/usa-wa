"""The citations artifact (#313): every published entity → the wires that attest it.

The replacement for the Postgres ``Citation`` chain that ``/provenance`` reads
today. Same question — *how do we know this?* — answered as a **stateless join**
rather than an append-only ledger, which is the whole #302 posture: a citation
the archive no longer supports simply stops being emitted, exactly as a span the
archive no longer asserts simply stops being published (retraction-as-absence).

**The chain.** Every staging row now carries the raw coordinates of the wire it
was read from (:func:`usa_wa_pipeline.staging.common.provenance`), and
``stg_raw_fetches`` carries what is known about each of those resources. So a
citation is just ``(entity_type, entity_id, source, resource_id)`` — the digest,
the fetch time and the URL are one join away and are not duplicated onto ~10^5
citation rows.

**Per entity kind:**

- **person** — every staging row carrying one of the entity's natural keys.
  Merge tombstones are followed: a citation pointing at a retired entity is a
  dangling one, and the tombstone is the only re-point signal a consumer gets.
- **organization** — the committee-roster, committee-membership and meeting
  wires that name the committee.
- **assignment** — **one citation per biennium the span covers**, at the wire
  that attests that biennium. This is the incumbent rule unchanged: it is what
  ``span_emit._ensure_citations`` does at emit time, moved to build time. The
  assignment is addressed by its 4-part span ``source_id``, which is its
  published identity — the serving tier keys assignments structurally, not by a
  ULID.

  Two deliberate departures from a naive biennium join, each measured against
  the real corpus rather than assumed:

  1. **A roster span is cited at the revision that lists the member, with no
     year filter.** The roster projector's §5 truncation bound derives a term
     from the *next* listing on a seat, so a span's bienniums routinely do not
     contain the listing year that attests it — Gary M. Odegaard's 1987-88
     Senate span rests on a 1985 listing. Filtering on the span's own years
     dropped 49 such spans to zero citations. The roster is one resource per
     revision anyway, so the filter could only ever turn one citation into none.
  2. **A WSL-family span that starts before the WSL archive's own earliest
     biennium, and that no wire reaches, falls back to the roster.** Those are
     the #228-deepened spans: the archive begins in 1991 and the roster is the
     source of record before it. The floor is read off the sponsor corpus, not
     hardcoded, and the fallback is whole-span rather than per-biennium so it
     states one thing — *the WSL archive attests none of this span; the roster
     does* — instead of mixing evidence. It resolves by the member's registered
     roster fold where the registry carries one, and otherwise at every roster
     wire: the fold that deepened such a span is the *resolver's*, and the
     roster↔WSL link rule only proposes folds with a 1991+ listing, so a member
     who left before then has no roster key to join on. Staging keeps only the
     newest revision, so "every roster wire" is a single resource — what is
     lost is which listing attests it, never which document.
- **role** — the union of its assignments' citations: a seat is attested by the
  wires that named someone sitting in it. Roles have no staging rows of their
  own (``role_for_span`` is a pure function of the seat), so there is nothing
  else honest to cite.

**One stated gap.** The SOS corroboration tier (``stg_sos_results``,
``stg_sos_filings``) is not per-entity addressable — its rows carry ballot names
and races, never a member id — so a House-Position span corroborated by SOS is
cited at its WSL evidence only. Under-citing is the safe direction; inventing a
name match to close the gap is not.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from clearinghouse_domain_legislative.span_kinds import KIND_COMMITTEE
from clearinghouse_domain_legislative.terms import bienniums_in_range
from usa_wa_adapter_legislature.roster_pdf.identity import identity_fold
from usa_wa_common.orgs import STRUCTURAL_ORGS

#: The published shape. ``entity_id`` is a registry ULID for person/organization
#: /role and a span ``source_id`` for assignment — see the module docstring.
CITATION_COLUMNS = ["entity_type", "entity_id", "source", "resource_id"]

ENTITY_PERSON = "person"
ENTITY_ORG = "organization"
ENTITY_ROLE = "role"
ENTITY_ASSIGNMENT = "assignment"

#: The WSL archive's numeric-member-id space; the roster-PDF's minted one.
SOURCE = "usa_wa_legislature"
ROSTER_SOURCE = "usa_wa_legislature_roster"

#: Natural-key namespaces, per the registry the crosswalks publish.
_ROSTER_NAMESPACE = ROSTER_SOURCE


@dataclass(frozen=True)
class CitationInputs:
    """Everything the join reads: two crosswalks, two dimensions, six staging sets."""

    person_crosswalk: list[dict[str, Any]] = field(default_factory=list)
    org_crosswalk: list[dict[str, Any]] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)
    assignments: list[dict[str, Any]] = field(default_factory=list)
    sponsors: list[dict[str, Any]] = field(default_factory=list)
    committee_members: list[dict[str, Any]] = field(default_factory=list)
    committees: list[dict[str, Any]] = field(default_factory=list)
    meetings: list[dict[str, Any]] = field(default_factory=list)
    pdc: list[dict[str, Any]] = field(default_factory=list)
    roster: list[dict[str, Any]] = field(default_factory=list)


def _text(value: Any) -> str | None:
    """A staging cell as text; pandas' NaN and the empty string both read as absent."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    text = str(value)
    return text or None


def _wire(row: dict[str, Any], *, what: str) -> tuple[str, str]:
    """The raw coordinates a staging row carries, refused if it lost them.

    Loud rather than silent: a row with no provenance would otherwise leave its
    entity uncited, and an uncited entity reads as a coverage gap in the archive
    rather than as the plumbing bug it is.
    """
    source, resource_id = _text(row.get("source")), _text(row.get("resource_id"))
    if not source or not resource_id:
        raise ValueError(
            f"{what} row carries no source/resource_id: {row!r} — staging rows must name "
            "the wire they were read from (#313), or the entities they feed become uncitable"
        )
    return source, resource_id


def _resolve_tombstones(crosswalk: Iterable[dict[str, Any]]) -> dict[str, str]:
    """``entity_id → the entity it now resolves to``, following merges to a fixed point."""
    merged = {
        row["entity_id"]: _text(row.get("merged_into"))
        for row in crosswalk
        if _text(row.get("merged_into"))
    }
    resolved: dict[str, str] = {}
    for entity_id in merged:
        seen = {entity_id}
        current = entity_id
        while (nxt := merged.get(current)) and nxt not in seen:
            seen.add(nxt)
            current = nxt
        resolved[entity_id] = current
    return resolved


def _key_index(crosswalk: list[dict[str, Any]]) -> dict[str, str]:
    """``natural_key → the LIVE entity it belongs to`` (tombstones followed)."""
    resolved = _resolve_tombstones(crosswalk)
    return {
        row["natural_key"]: resolved.get(row["entity_id"], row["entity_id"]) for row in crosswalk
    }


def _roster_folds(crosswalk: list[dict[str, Any]]) -> tuple[dict[str, str], int]:
    """``fold → entity``, dropping folds two entities share (the Jr/Sr signature).

    A roster staging row carries a printed name, not a natural key, so the fold
    is the only join available — and where it is ambiguous, citing either half
    of a Jr/Sr pair would attribute one man's career to the other.
    """
    by_fold: dict[str, set[str]] = {}
    resolved = _resolve_tombstones(crosswalk)
    for row in crosswalk:
        if row.get("key_namespace") != _ROSTER_NAMESPACE:
            continue
        fold = str(row["key_value"]).rsplit(":", 1)[0]
        entity = resolved.get(row["entity_id"], row["entity_id"])
        by_fold.setdefault(fold, set()).add(entity)
    unique = {fold: next(iter(ids)) for fold, ids in by_fold.items() if len(ids) == 1}
    return unique, len(by_fold) - len(unique)


def _span_source_id(assignment: dict[str, Any]) -> str:
    """The assignment's published identity: ``{member}:{kind}:{disc}:{start}``."""
    return ":".join(
        str(assignment[column])
        for column in ("member_id", "span_kind", "span_discriminator", "span_start_biennium")
    )


def _covered_bienniums(assignment: dict[str, Any], *, newest: str | None) -> list[str]:
    """The bienniums a span covers; an open span runs to the newest attestation."""
    start = _text(assignment.get("span_start_biennium"))
    if start is None:
        return []
    end = _text(assignment.get("span_end_biennium")) or newest
    if end is None or end < start:
        return [start]
    return bienniums_in_range(start, end)


def citation_rows(inputs: CitationInputs) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The whole citations table plus its counters. Pure; deterministic order."""
    cited: set[tuple[str, str, str, str]] = set()

    def cite(entity_type: str, entity_id: str, wire: tuple[str, str]) -> None:
        cited.add((entity_type, entity_id, *wire))

    # ---- persons -------------------------------------------------------
    person_by_key = _key_index(inputs.person_crosswalk)
    folds, ambiguous_folds = _roster_folds(inputs.person_crosswalk)

    for rows, what, key_of in (
        (inputs.sponsors, "sponsor", lambda r: f"{SOURCE}:{_text(r.get('member_id'))}"),
        (
            inputs.committee_members,
            "committee member",
            lambda r: f"{SOURCE}:{_text(r.get('member_id'))}",
        ),
        (inputs.pdc, "pdc winner", lambda r: f"wa_pdc:{_text(r.get('person_id'))}"),
    ):
        for row in rows:
            wire = _wire(row, what=what)
            entity = person_by_key.get(key_of(row))
            if entity:
                cite(ENTITY_PERSON, entity, wire)

    for row in inputs.roster:
        wire = _wire(row, what="roster")
        name = _text(row.get("name"))
        if not name:
            continue
        entity = folds.get(identity_fold(name))
        if entity:
            cite(ENTITY_PERSON, entity, wire)

    # ---- organizations -------------------------------------------------
    org_by_key = _key_index(inputs.org_crosswalk)
    for rows, what in (
        (inputs.committees, "committee"),
        (inputs.committee_members, "committee member"),
        (inputs.meetings, "meeting"),
    ):
        for row in rows:
            wire = _wire(row, what=what)
            entity = org_by_key.get(f"{SOURCE}:{_text(row.get('committee_id'))}")
            if entity:
                cite(ENTITY_ORG, entity, wire)

    # ---- assignments ---------------------------------------------------
    # Indexed by exactly the key each family's spans are keyed on, so an
    # assignment's citations are a lookup per covered biennium rather than a
    # scan of the whole staging corpus.
    sponsor_wires: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for row in inputs.sponsors:
        wire = _wire(row, what="sponsor")
        member, biennium = _text(row.get("member_id")), _text(row.get("biennium"))
        if member and biennium:
            sponsor_wires.setdefault((member, biennium), set()).add(wire)

    membership_wires: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    for row in inputs.committee_members:
        wire = _wire(row, what="committee member")
        member = _text(row.get("member_id"))
        committee = _text(row.get("committee_id"))
        biennium = _text(row.get("biennium"))
        if member and committee and biennium:
            membership_wires.setdefault((member, committee, biennium), set()).add(wire)

    roster_wires: dict[str, set[tuple[str, str]]] = {}
    for row in inputs.roster:
        wire = _wire(row, what="roster")
        name = _text(row.get("name"))
        if name:
            roster_wires.setdefault(identity_fold(name), set()).add(wire)

    # entity → the roster folds registered to it, for the deepening fallback: a
    # pre-1991 WSL-family span is attested by the roster listing of the SAME
    # person, which only the crosswalk can connect to a numeric member id.
    folds_by_entity: dict[str, set[str]] = {}
    for fold, entity in folds.items():
        folds_by_entity.setdefault(entity, set()).add(fold)

    # The WSL archive's own reach, read off the corpus rather than hardcoded as
    # 1991: a span starting before its earliest biennium cannot have a sponsor
    # wire, so the roster is the only thing that could attest it.
    archive_bienniums = {biennium for _, biennium in sponsor_wires}
    archive_floor = min(archive_bienniums, default=None)
    newest_biennium = max(archive_bienniums, default=None)
    every_roster_wire = {wire for wires in roster_wires.values() for wire in wires}
    by_role_key: dict[str, set[tuple[str, str]]] = {}
    uncited_assignments = 0
    for assignment in inputs.assignments:
        source_id = _span_source_id(assignment)
        member = _text(assignment.get("member_id"))
        wires: set[tuple[str, str]] = set()
        if _text(assignment.get("source")) == ROSTER_SOURCE:
            # A roster identity is `<fold>:<first-session-year>`; the fold half
            # is the join. No year filter — see the module docstring.
            wires = set(roster_wires.get((member or "").rsplit(":", 1)[0], set()))
        elif member:
            for biennium in _covered_bienniums(assignment, newest=newest_biennium):
                if _text(assignment.get("span_kind")) == KIND_COMMITTEE:
                    key = (member, _text(assignment.get("span_discriminator")) or "", biennium)
                    wires |= membership_wires.get(key, set())
                else:
                    wires |= sponsor_wires.get((member, biennium), set())
            start = _text(assignment.get("span_start_biennium"))
            deepened = start is not None and archive_floor is not None and start < archive_floor
            if not wires and deepened:
                # The #228 deepening: the roster is the source of record here.
                # By the member's REGISTERED fold when the registry carries one
                # — and otherwise at every roster wire, because the fold that
                # deepened this span is the resolver's, and the roster↔WSL link
                # rule only proposes folds with a 1991+ listing, so a member who
                # left before then has no roster key at all. Staging keeps only
                # the newest revision, so "every roster wire" is one resource:
                # the precision lost is which listing, not which document.
                for fold in folds_by_entity.get(_text(assignment.get("entity_id")) or "", set()):
                    wires |= roster_wires.get(fold, set())
                wires = wires or set(every_roster_wire)
        if not wires:
            uncited_assignments += 1
        for wire in wires:
            cite(ENTITY_ASSIGNMENT, source_id, wire)
        role_key = _text(assignment.get("role_key"))
        if role_key:
            by_role_key.setdefault(role_key, set()).update(wires)

    # ---- roles ---------------------------------------------------------
    unregistered_roles = 0
    for role in inputs.roles:
        entity = _text(role.get("entity_id"))
        if not entity:
            unregistered_roles += 1
            continue
        for wire in by_role_key.get(_text(role.get("role_key")) or "", set()):
            cite(ENTITY_ROLE, entity, wire)

    rows = [dict(zip(CITATION_COLUMNS, row, strict=True)) for row in sorted(cited)]
    uncited_persons = set(person_by_key.values()) - {
        row["entity_id"] for row in rows if row["entity_type"] == ENTITY_PERSON
    }
    # The structural orgs (the Legislature, the two chambers, the eight parties)
    # are DEFINITIONAL — `usa_wa_common.orgs.STRUCTURAL_ORGS`, not read off any
    # wire — so they are uncitable by construction and counted apart. Folding
    # them into `uncited_organizations` would put a permanent floor of 11 under
    # a counter whose whole use is to be gated at zero.
    structural = {
        entity for key, entity in org_by_key.items() if key.partition(":")[2] in STRUCTURAL_ORGS
    }
    uncited_orgs = (
        set(org_by_key.values())
        - structural
        - {row["entity_id"] for row in rows if row["entity_type"] == ENTITY_ORG}
    )
    counters = {
        "citations": len(rows),
        "uncited_persons": len(uncited_persons),
        "uncited_organizations": len(uncited_orgs),
        "structural_organizations": len(structural),
        "uncited_assignments": uncited_assignments,
        "unregistered_roles": unregistered_roles,
        "ambiguous_roster_folds": ambiguous_folds,
    }
    return rows, counters
