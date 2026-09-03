"""Washington jurisdiction vocabulary (#310): the locally-owned registry.

The seat model's district axis (Role.jurisdiction_id) FKs the
clearinghouse_core.jurisdictions table, whose rows the PM sidecar used to
mirror. Under #302 usa-wa never reads PM, so ownership transfers HERE: these
facts are the registry, seed_jurisdictions asserts them into the table, and
the sidecar's jurisdiction sync becomes redundant (retired at #314).

Extracted verbatim from the production mirror on 2026-09-03 (100 rows: country,
state, 49 legislative districts, 39 counties, 10 congressional districts) so
the ownership transfer is a proven no-op; from then on this module is the
source of truth — a redistricting edit happens here first. Rows outside the
vocabulary (e.g. the PM-discovered usa-wa-city-seattle) are left alone by
the seed and reported, never deleted.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JurisdictionFact:
    """One WA jurisdiction: slug (the natural key), display name, type slug."""

    slug: str
    name: str
    type_slug: str


#: Types the facts below reference, seeded on demand: slug -> display name.
JURISDICTION_TYPE_NAMES = {
    "congressional_district": "Congressional District",
    "country": "Country",
    "county": "County",
    "legislative_district": "Legislative District",
    "state": "State",
}

WA_JURISDICTIONS: tuple[JurisdictionFact, ...] = (
    JurisdictionFact(
        "usa-wa-cd-1", "Washington 1st Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-10", "Washington 10th Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-2", "Washington 2nd Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-3", "Washington 3rd Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-4", "Washington 4th Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-5", "Washington 5th Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-6", "Washington 6th Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-7", "Washington 7th Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-8", "Washington 8th Congressional District", "congressional_district"
    ),
    JurisdictionFact(
        "usa-wa-cd-9", "Washington 9th Congressional District", "congressional_district"
    ),
    JurisdictionFact("usa", "United States of America", "country"),
    JurisdictionFact("usa-wa-county-adams", "Adams County", "county"),
    JurisdictionFact("usa-wa-county-asotin", "Asotin County", "county"),
    JurisdictionFact("usa-wa-county-benton", "Benton County", "county"),
    JurisdictionFact("usa-wa-county-chelan", "Chelan County", "county"),
    JurisdictionFact("usa-wa-county-clallam", "Clallam County", "county"),
    JurisdictionFact("usa-wa-county-clark", "Clark County", "county"),
    JurisdictionFact("usa-wa-county-columbia", "Columbia County", "county"),
    JurisdictionFact("usa-wa-county-cowlitz", "Cowlitz County", "county"),
    JurisdictionFact("usa-wa-county-douglas", "Douglas County", "county"),
    JurisdictionFact("usa-wa-county-ferry", "Ferry County", "county"),
    JurisdictionFact("usa-wa-county-franklin", "Franklin County", "county"),
    JurisdictionFact("usa-wa-county-garfield", "Garfield County", "county"),
    JurisdictionFact("usa-wa-county-grant", "Grant County", "county"),
    JurisdictionFact("usa-wa-county-grays_harbor", "Grays Harbor County", "county"),
    JurisdictionFact("usa-wa-county-island", "Island County", "county"),
    JurisdictionFact("usa-wa-county-jefferson", "Jefferson County", "county"),
    JurisdictionFact("usa-wa-county-king", "King County", "county"),
    JurisdictionFact("usa-wa-county-kitsap", "Kitsap County", "county"),
    JurisdictionFact("usa-wa-county-kittitas", "Kittitas County", "county"),
    JurisdictionFact("usa-wa-county-klickitat", "Klickitat County", "county"),
    JurisdictionFact("usa-wa-county-lewis", "Lewis County", "county"),
    JurisdictionFact("usa-wa-county-lincoln", "Lincoln County", "county"),
    JurisdictionFact("usa-wa-county-mason", "Mason County", "county"),
    JurisdictionFact("usa-wa-county-okanogan", "Okanogan County", "county"),
    JurisdictionFact("usa-wa-county-pacific", "Pacific County", "county"),
    JurisdictionFact("usa-wa-county-pend_oreille", "Pend Oreille County", "county"),
    JurisdictionFact("usa-wa-county-pierce", "Pierce County", "county"),
    JurisdictionFact("usa-wa-county-san_juan", "San Juan County", "county"),
    JurisdictionFact("usa-wa-county-skagit", "Skagit County", "county"),
    JurisdictionFact("usa-wa-county-skamania", "Skamania County", "county"),
    JurisdictionFact("usa-wa-county-snohomish", "Snohomish County", "county"),
    JurisdictionFact("usa-wa-county-spokane", "Spokane County", "county"),
    JurisdictionFact("usa-wa-county-stevens", "Stevens County", "county"),
    JurisdictionFact("usa-wa-county-thurston", "Thurston County", "county"),
    JurisdictionFact("usa-wa-county-wahkiakum", "Wahkiakum County", "county"),
    JurisdictionFact("usa-wa-county-walla_walla", "Walla Walla County", "county"),
    JurisdictionFact("usa-wa-county-whatcom", "Whatcom County", "county"),
    JurisdictionFact("usa-wa-county-whitman", "Whitman County", "county"),
    JurisdictionFact("usa-wa-county-yakima", "Yakima County", "county"),
    JurisdictionFact("usa-wa-ld-1", "Washington Legislative District 1", "legislative_district"),
    JurisdictionFact("usa-wa-ld-10", "Washington Legislative District 10", "legislative_district"),
    JurisdictionFact("usa-wa-ld-11", "Washington Legislative District 11", "legislative_district"),
    JurisdictionFact("usa-wa-ld-12", "Washington Legislative District 12", "legislative_district"),
    JurisdictionFact("usa-wa-ld-13", "Washington Legislative District 13", "legislative_district"),
    JurisdictionFact("usa-wa-ld-14", "Washington Legislative District 14", "legislative_district"),
    JurisdictionFact("usa-wa-ld-15", "Washington Legislative District 15", "legislative_district"),
    JurisdictionFact("usa-wa-ld-16", "Washington Legislative District 16", "legislative_district"),
    JurisdictionFact("usa-wa-ld-17", "Washington Legislative District 17", "legislative_district"),
    JurisdictionFact("usa-wa-ld-18", "Washington Legislative District 18", "legislative_district"),
    JurisdictionFact("usa-wa-ld-19", "Washington Legislative District 19", "legislative_district"),
    JurisdictionFact("usa-wa-ld-2", "Washington Legislative District 2", "legislative_district"),
    JurisdictionFact("usa-wa-ld-20", "Washington Legislative District 20", "legislative_district"),
    JurisdictionFact("usa-wa-ld-21", "Washington Legislative District 21", "legislative_district"),
    JurisdictionFact("usa-wa-ld-22", "Washington Legislative District 22", "legislative_district"),
    JurisdictionFact("usa-wa-ld-23", "Washington Legislative District 23", "legislative_district"),
    JurisdictionFact("usa-wa-ld-24", "Washington Legislative District 24", "legislative_district"),
    JurisdictionFact("usa-wa-ld-25", "Washington Legislative District 25", "legislative_district"),
    JurisdictionFact("usa-wa-ld-26", "Washington Legislative District 26", "legislative_district"),
    JurisdictionFact("usa-wa-ld-27", "Washington Legislative District 27", "legislative_district"),
    JurisdictionFact("usa-wa-ld-28", "Washington Legislative District 28", "legislative_district"),
    JurisdictionFact("usa-wa-ld-29", "Washington Legislative District 29", "legislative_district"),
    JurisdictionFact("usa-wa-ld-3", "Washington Legislative District 3", "legislative_district"),
    JurisdictionFact("usa-wa-ld-30", "Washington Legislative District 30", "legislative_district"),
    JurisdictionFact("usa-wa-ld-31", "Washington Legislative District 31", "legislative_district"),
    JurisdictionFact("usa-wa-ld-32", "Washington Legislative District 32", "legislative_district"),
    JurisdictionFact("usa-wa-ld-33", "Washington Legislative District 33", "legislative_district"),
    JurisdictionFact("usa-wa-ld-34", "Washington Legislative District 34", "legislative_district"),
    JurisdictionFact("usa-wa-ld-35", "Washington Legislative District 35", "legislative_district"),
    JurisdictionFact("usa-wa-ld-36", "Washington Legislative District 36", "legislative_district"),
    JurisdictionFact("usa-wa-ld-37", "Washington Legislative District 37", "legislative_district"),
    JurisdictionFact("usa-wa-ld-38", "Washington Legislative District 38", "legislative_district"),
    JurisdictionFact("usa-wa-ld-39", "Washington Legislative District 39", "legislative_district"),
    JurisdictionFact("usa-wa-ld-4", "Washington Legislative District 4", "legislative_district"),
    JurisdictionFact("usa-wa-ld-40", "Washington Legislative District 40", "legislative_district"),
    JurisdictionFact("usa-wa-ld-41", "Washington Legislative District 41", "legislative_district"),
    JurisdictionFact("usa-wa-ld-42", "Washington Legislative District 42", "legislative_district"),
    JurisdictionFact("usa-wa-ld-43", "Washington Legislative District 43", "legislative_district"),
    JurisdictionFact("usa-wa-ld-44", "Washington Legislative District 44", "legislative_district"),
    JurisdictionFact("usa-wa-ld-45", "Washington Legislative District 45", "legislative_district"),
    JurisdictionFact("usa-wa-ld-46", "Washington Legislative District 46", "legislative_district"),
    JurisdictionFact("usa-wa-ld-47", "Washington Legislative District 47", "legislative_district"),
    JurisdictionFact("usa-wa-ld-48", "Washington Legislative District 48", "legislative_district"),
    JurisdictionFact("usa-wa-ld-49", "Washington Legislative District 49", "legislative_district"),
    JurisdictionFact("usa-wa-ld-5", "Washington Legislative District 5", "legislative_district"),
    JurisdictionFact("usa-wa-ld-6", "Washington Legislative District 6", "legislative_district"),
    JurisdictionFact("usa-wa-ld-7", "Washington Legislative District 7", "legislative_district"),
    JurisdictionFact("usa-wa-ld-8", "Washington Legislative District 8", "legislative_district"),
    JurisdictionFact("usa-wa-ld-9", "Washington Legislative District 9", "legislative_district"),
    JurisdictionFact("usa-wa", "Washington", "state"),
)


def wa_jurisdiction_facts() -> tuple[JurisdictionFact, ...]:
    """The declared registry, LDs first is not guaranteed — key on slug."""
    return WA_JURISDICTIONS
