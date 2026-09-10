"""Conformed persons + organizations (#309): survivorship over staging.

Stateless joins of the registry crosswalk against staging attributes:

- **persons** — one row per LIVE entity (merge tombstones drop out here; the
  crosswalk dataset still carries them). Name survivorship per the spec:
  roster > WSL > PDC — the roster's display names are curated print, WSL's
  are live-web, PDC's are filing-office ALLCAPS (title-cased as a last
  resort). Within a source, the newest attestation wins (a marriage rename
  takes the latest roster/biennium form) — but only among rows that actually
  carry a name (:func:`_name`, #364).
- **organizations** — committee attributes from the newest biennium's roster
  wire; bodies only ever seen in meeting wires (Joint/`Other`, #39) fall back
  to their meeting ref names. Names go through the same :func:`_name` screen as
  a person's (CR 5): a committee wire has never answered blank, but an
  organization's name is published under the same contract.
"""

from __future__ import annotations

from typing import Any

from usa_wa_adapter_legislature.roster_pdf.identity import identity_fold
from usa_wa_common.orgs import STRUCTURAL_ORGS
from usa_wa_pipeline.conformed.crosswalk import merge_map, resolve_merged

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


def _name(value: Any) -> str | None:
    """A source's name field as a NAME, or ``None`` — blank is absent (#364).

    Two upstream shapes, one rule. `GetSponsors` returns a name-blanked STUB
    for a superseded / departed (member, chamber-tenure) — a real ``Id``, a
    single-space ``Name``, no first/last, no district (the shape
    `normalize.members.is_person` has always screened on the canonical path).
    Truthiness does not screen it: ``' '`` is truthy, so the stub read as the
    member's newest attestation and four sitting legislators — Tina Orwall,
    Tim Sheldon, Robert Sutherland, Simon Sefzik — published ``' '`` as their
    legal name until power-map#497 found it downstream (#364).

    Second, real names arrive untrimmed (WSL's ``'Marlo Braun '``, PDC's
    ``'MICHAEL JAMES BAUMGARTNER '``), and the stored name is the name.

    Third, a value that is not a string is not a name (CR 1, CR 9). These rows
    come from ``.df().to_dict("records")``, where a null in a non-object column
    arrives as one of pandas' three null objects — and ``str()`` renders them
    ``'nan'``, ``'<NA>'``, ``'NaT'``, each a perfectly plausible name. Screening
    on the TYPE rather than testing for each of them is what makes this total:
    the identity trick ``value != value`` catches ``NaN`` and then RAISES on
    ``pd.NA`` (its truth value is ambiguous), which traded a silent wrong name
    for an uncaught abort of the nightly build. Every name field in all three
    sources is VARCHAR by construction, so nothing legitimate is turned away.
    Today's nulls arrive as ``None`` and none of this can fire; it is guarded
    because a silent wrong name is what #364 exists to end.

    Applied to all three sources, not just WSL's. Precedence is a chain, so a
    blank winning at any link publishes whitespace just the same; and skipping
    it here rather than dropping the row keeps survivorship's meaning — a
    source that has no name for someone does not veto the sources below it.

    Deliberately at the conformed tier, not in staging: staging re-parses the
    archive and holds no policy (`staging.wsl.meeting_rows` says so out loud),
    and nulling the stub there would erase the evidence that the wire answered
    with one. What a name IS is a survivorship question, and this is where
    survivorship lives.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _live_entities(crosswalk: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """entity_id → its keys, tombstones RESOLVED to the survivor (#366).

    A merge re-points, it does not delete: the loser's keys still name the same
    real person or body, now under the survivor's ULID, and the tombstone is the
    published crosswalk's only re-point signal. So the loser's rows are
    attributed to whoever it merged into rather than dropped — otherwise the
    survivor is published stripped of exactly the identity the merge gave it.

    This is the rule `spans.entity_index` and `citations._key_index` already
    apply; this function was the one consumer that did not, and the first real
    merge showed what that costs. Denny Heck's 1977-85 party span and his roster
    citation followed the merge; his NAME did not, because his roster key lived
    on the tombstoned row — so `persons` stopped publishing "Dennis L. Heck" and
    published nothing for him instead, which is worse than the duplicate the
    merge was resolving.

    Chains resolve transitively, through the one shared walk
    (:mod:`usa_wa_pipeline.conformed.crosswalk`) rather than a local copy — four
    copies of this rule is what #366 WAS.
    """
    merges = merge_map(crosswalk)
    out: dict[str, list[dict[str, Any]]] = {}
    for row in crosswalk:
        out.setdefault(resolve_merged(merges, str(row["entity_id"])), []).append(row)
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
        member_id, name = row.get("member_id"), _name(row.get("name"))
        if member_id and name:
            current = wsl_latest.get(member_id)
            if current is None or row["biennium"] > current[0]:
                wsl_latest[member_id] = (row["biennium"], name)

    roster_latest: dict[str, tuple[int, str]] = {}
    roster_first_year: dict[str, int] = {}
    for row in roster:
        name = _name(row.get("name"))
        if not name:
            continue
        fold = identity_fold(name)
        year = int(row["year"])
        roster_first_year[fold] = min(roster_first_year.get(fold, year), year)
        current = roster_latest.get(fold)
        if current is None or year > current[0]:
            roster_latest[fold] = (year, name)

    pdc_names = {
        row["person_id"]: _name(row.get("filer_name")) for row in pdc if row.get("person_id")
    }

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
                    "name": _name(latest.get("name")),
                    "long_name": _name(latest.get("long_name")),
                    # NOT screened (CR 8): 35 committee acronyms are space-padded
                    # in the wire (`'AG  '`), so trimming here would change 35
                    # published values in a column this review never looked at.
                    # An acronym is not a name; that is its own decision.
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
                "name": _name(ref.get("committee_name")) if ref else None,
                "long_name": None,
                "acronym": None,
                "agency": ref.get("committee_agency") if ref else None,
                "org_type": "other",
                "first_biennium": None,
                "last_biennium": None,
            }
        )
    return rows
