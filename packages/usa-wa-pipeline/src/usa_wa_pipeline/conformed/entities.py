"""Conformed persons + organizations (#309): survivorship over staging.

Stateless joins of the registry crosswalk against staging attributes:

- **persons** — one row per LIVE entity (merge tombstones drop out here; the
  crosswalk dataset still carries them). Name survivorship per the spec:
  roster > WSL > PDC — the roster's display names are curated print, WSL's
  are live-web, PDC's are filing-office ALLCAPS (title-cased as a last
  resort). Within a source, the newest attestation wins (a marriage rename
  takes the latest roster/biennium form).
- **organizations** — committee attributes from the newest biennium's roster
  wire; bodies only ever seen in meeting wires (Joint/`Other`, #39) fall back
  to their meeting ref names.
"""

from __future__ import annotations

from typing import Any

from usa_wa_adapter_legislature.roster_pdf.identity import identity_fold
from usa_wa_common.orgs import STRUCTURAL_ORGS

PERSON_COLUMNS = ["entity_id", "name_full", "name_source"]
ORG_COLUMNS = [
    "entity_id",
    "name",
    "long_name",
    "acronym",
    "agency",
    "org_type",
    "first_biennium",
    "last_biennium",
]

#: Canonical's classification: House/Senate standing committees are
#: ``committee``; Joint/`Other` bodies (the #39 meeting-derived class) are
#: ``other``.
_COMMITTEE_TYPES = {"House": "committee", "Senate": "committee"}


def _live_entities(crosswalk: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """entity_id → its keys, live entities only."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in crosswalk:
        if row["merged_into"] is not None:
            continue
        out.setdefault(row["entity_id"], []).append(row)
    return out


def person_rows(
    crosswalk: list[dict[str, Any]],
    *,
    sponsors: list[dict[str, Any]],
    roster: list[dict[str, Any]],
    pdc: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One conformed person per live registry entity."""
    wsl_latest: dict[str, tuple[str, str]] = {}
    for row in sponsors:
        member_id, name = row.get("member_id"), row.get("name")
        if member_id and name:
            current = wsl_latest.get(member_id)
            if current is None or row["biennium"] > current[0]:
                wsl_latest[member_id] = (row["biennium"], name)

    roster_latest: dict[str, tuple[int, str]] = {}
    roster_first_year: dict[str, int] = {}
    for row in roster:
        name = row.get("name")
        if not name:
            continue
        fold = identity_fold(name)
        year = int(row["year"])
        roster_first_year[fold] = min(roster_first_year.get(fold, year), year)
        current = roster_latest.get(fold)
        if current is None or year > current[0]:
            roster_latest[fold] = (year, name)

    pdc_names = {row["person_id"]: row.get("filer_name") for row in pdc if row.get("person_id")}

    rows = []
    for entity_id, keys in sorted(_live_entities(crosswalk).items()):
        name_full: str | None = None
        name_source: str | None = None
        for key in keys:
            if key["key_namespace"] == "usa_wa_legislature_roster":
                fold = key["key_value"].rsplit(":", 1)[0]
                if fold in roster_latest:
                    name_full, name_source = roster_latest[fold][1], "roster"
                    break
        if name_full is None:
            for key in keys:
                if key["key_namespace"] == "usa_wa_legislature":
                    hit = wsl_latest.get(key["key_value"])
                    if hit:
                        name_full, name_source = hit[1], "wsl"
                        break
        if name_full is None:
            for key in keys:
                if key["key_namespace"] == "wa_pdc":
                    raw = pdc_names.get(key["key_value"])
                    if raw:
                        name_full, name_source = raw.title(), "pdc"
                        break
        rows.append({"entity_id": entity_id, "name_full": name_full, "name_source": name_source})
    return rows


def org_rows(
    crosswalk: list[dict[str, Any]],
    *,
    committees: list[dict[str, Any]],
    meetings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One conformed organization per live registry entity."""
    by_committee: dict[str, list[dict[str, Any]]] = {}
    for row in committees:
        if row.get("committee_id"):
            by_committee.setdefault(row["committee_id"], []).append(row)
    meeting_refs: dict[str, dict[str, Any]] = {}
    for row in meetings:
        cid = row.get("committee_id")
        if cid and cid not in meeting_refs:
            meeting_refs[cid] = row

    rows = []
    for entity_id, keys in sorted(_live_entities(crosswalk).items()):
        committee_ids = [k["key_value"] for k in keys if k["key_namespace"] == "usa_wa_legislature"]
        structural = next(
            (STRUCTURAL_ORGS[cid] for cid in committee_ids if cid in STRUCTURAL_ORGS), None
        )
        if structural is not None:
            rows.append(
                {
                    "entity_id": entity_id,
                    "name": structural.name,
                    "long_name": None,
                    "acronym": None,
                    "agency": None,
                    "org_type": structural.org_type,
                    "first_biennium": None,
                    "last_biennium": None,
                }
            )
            continue
        attested = sorted(
            (r for cid in committee_ids for r in by_committee.get(cid, [])),
            key=lambda r: r["biennium"],
        )
        if attested:
            latest = attested[-1]
            rows.append(
                {
                    "entity_id": entity_id,
                    "name": latest.get("name"),
                    "long_name": latest.get("long_name"),
                    "acronym": latest.get("acronym"),
                    "agency": latest.get("agency"),
                    "org_type": _COMMITTEE_TYPES.get(latest.get("agency"), "other"),
                    "first_biennium": attested[0]["biennium"],
                    "last_biennium": latest["biennium"],
                }
            )
            continue
        ref = next((meeting_refs[cid] for cid in committee_ids if cid in meeting_refs), None)
        rows.append(
            {
                "entity_id": entity_id,
                "name": ref.get("committee_name") if ref else None,
                "long_name": None,
                "acronym": None,
                "agency": ref.get("committee_agency") if ref else None,
                "org_type": "other",
                "first_biennium": None,
                "last_biennium": None,
            }
        )
    return rows
