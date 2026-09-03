"""Roles and seats as deterministic structural keys (#309 part 2, increment 4).

A **Role** is a named slot within an Organization; an **Assignment** binds one
in time (ONTOLOGY.md § 2). The span already carries the slot's identity as
``(kind, discriminator)`` — this module derives the *key* the Postgres tier
mints from that pair, so a published assignment can name its role and a
consumer joins a real dimension instead of re-deriving one from a string.

**Structural, not registered.** Unlike persons and organizations, a role needs
no registry: its key is a pure function of the seat it names, identical on
every run and in every deployment. That is the whole point of the increment —
``seat:house:ld-5:position-1`` means the same thing to us, to Power Map (it
aligns 1:1 with PM's seat match key) and to a consumer reading the CSV, with no
ULID mediation. Only the *organization* the slot belongs to is registered, and
that comes in from the crosswalk.

Every key function is imported UNCHANGED — ``party_role_source_id``,
``committee_member_role_source_id``, ``senate_seat_role_source_id`` from the
adapter's normalizer, ``house_seat_role_source_id`` and
``parse_house_span_discriminator`` from the WA vocabulary. Re-deriving any of
them here would fork the seat identity from the tier that already publishes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clearinghouse_domain_legislative.span_kinds import (
    KIND_COMMITTEE,
    KIND_HOUSE,
    KIND_PARTY,
    KIND_SENATE,
)
from usa_wa_adapter_legislature.normalize.members import (
    committee_member_role_source_id,
    party_role_source_id,
    senate_seat_role_source_id,
)
from usa_wa_common.seats import house_seat_role_source_id, parse_house_span_discriminator

#: The source every role key is asserted under — roles are WSL-space slots even
#: when the tenure filling them came from the roster PDF.
SOURCE = "usa_wa_legislature"

#: Structural org source_ids the seats hang from.
HOUSE_ORG = "usa_wa_house"
SENATE_ORG = "usa_wa_senate"

ROLE_COLUMNS = [
    "entity_id",
    "role_key",
    "role_type",
    "name",
    "span_kind",
    "span_discriminator",
    "org_source_id",
    "org_entity_id",
    "district",
    "qualifier",
]


@dataclass(frozen=True)
class Role:
    """One named slot: its deterministic key, its type, and the org it sits in."""

    role_key: str
    role_type: str
    name: str
    span_kind: str
    span_discriminator: str
    org_source_id: str
    district: int | None = None
    qualifier: str | None = None


def role_for_span(span_kind: str, span_discriminator: str) -> Role:
    """``(kind, discriminator)`` → the Role that pair names.

    Raises ``ValueError`` for a kind with no mapping, or a discriminator the
    mapped key function cannot parse. Both are refusals rather than skips: a
    published assignment names its role, so an unmapped kind would point at a
    dimension row nothing defines — and guessing a key would fork the seat
    identity from the tier that already publishes it.
    """
    try:
        if span_kind == KIND_PARTY:
            return Role(
                role_key=party_role_source_id(span_discriminator),
                role_type="party_member",
                name="Member",
                span_kind=span_kind,
                span_discriminator=span_discriminator,
                org_source_id=f"party-{span_discriminator}",
            )
        if span_kind == KIND_COMMITTEE:
            return Role(
                role_key=committee_member_role_source_id(span_discriminator),
                role_type="committee_member",
                name="Member",
                span_kind=span_kind,
                span_discriminator=span_discriminator,
                # the committee is its own org — not a structural one
                org_source_id=span_discriminator,
            )
        if span_kind == KIND_SENATE:
            ld = int(span_discriminator)
            return Role(
                role_key=senate_seat_role_source_id(ld),
                role_type="state_senator",
                name=f"Washington State Senator, LD-{ld}",
                span_kind=span_kind,
                span_discriminator=span_discriminator,
                org_source_id=SENATE_ORG,
                district=ld,
            )
        if span_kind == KIND_HOUSE:
            ld, qualifier = parse_house_span_discriminator(span_discriminator)
            return Role(
                role_key=house_seat_role_source_id(ld, qualifier),
                role_type="state_representative",
                name=f"Washington State Representative, LD-{ld}, {qualifier}",
                span_kind=span_kind,
                span_discriminator=span_discriminator,
                org_source_id=HOUSE_ORG,
                district=ld,
                qualifier=qualifier,
            )
    except (IndexError, ValueError) as exc:
        raise ValueError(
            f"unparseable span discriminator {span_discriminator!r} for kind {span_kind!r}: "
            "the role key cannot be derived, and guessing one would publish an assignment "
            f"pointing at a role nothing defines ({exc})"
        ) from exc
    raise ValueError(
        f"no role mapping for span kind {span_kind!r} — a new kind must be given one "
        "deliberately, not defaulted"
    )


def role_rows(
    assignments: list[dict[str, Any]],
    org_by_key: dict[str, str],
    role_by_key: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Published assignments → the role dimension they point at, deduplicated.

    One row per slot, not per tenure: two members holding one seat across time
    are one role. Two crosswalks join here — ``role_by_key`` gives the role its
    own ULID (#313), ``org_by_key`` the organization it sits in — and **neither
    is allowed to drop a row**. That asymmetry with
    :func:`~usa_wa_pipeline.conformed.spans.assignment_rows` is deliberate: an
    assignment with no person is headless and must drop, but a seat exists
    whether or not the registry has reached it, and dropping it would delete the
    dimension row a published assignment already names. The nightly runs
    ``dbt build → registrar → publish``, so a brand-new seat is unregistered in
    the build that first sees it and bound by the next; the counters are what
    make that one-run latency visible rather than silent.

    ``role_key`` stays first-class beside ``entity_id``: it is what PM matches a
    seat on, and mediating it away is exactly what #309 refused.
    """
    seen: dict[str, Role] = {}
    for row in assignments:
        role = role_for_span(str(row["span_kind"]), str(row["span_discriminator"]))
        seen.setdefault(role.role_key, role)

    rows: list[dict[str, Any]] = []
    counters = {"roles": len(seen), "unregistered_orgs": 0, "unregistered_roles": 0}
    for role in sorted(seen.values(), key=lambda r: r.role_key):
        entity_id = org_by_key.get(f"{SOURCE}:{role.org_source_id}")
        if entity_id is None:
            counters["unregistered_orgs"] += 1
        role_entity_id = role_by_key.get(f"{SOURCE}:{role.role_key}")
        if role_entity_id is None:
            counters["unregistered_roles"] += 1
        rows.append(
            {
                "entity_id": role_entity_id,
                "role_key": role.role_key,
                "role_type": role.role_type,
                "name": role.name,
                "span_kind": role.span_kind,
                "span_discriminator": role.span_discriminator,
                "org_source_id": role.org_source_id,
                "org_entity_id": entity_id,
                "district": role.district,
                "qualifier": role.qualifier,
            }
        )
    return rows, counters
