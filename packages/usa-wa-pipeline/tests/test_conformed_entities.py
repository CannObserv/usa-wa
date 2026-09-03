"""Conformed persons/orgs survivorship (#309): registry ⨝ staging, pure."""

import usa_wa_pipeline.conformed.entities as mod
from usa_wa_pipeline.conformed.entities import org_rows, person_rows

CROSSWALK = [
    {
        "entity_id": "01A",
        "key_namespace": "usa_wa_legislature",
        "key_value": "27992",
        "merged_into": None,
    },
    {"entity_id": "01A", "key_namespace": "wa_pdc", "key_value": "7710", "merged_into": None},
    {
        "entity_id": "01A",
        "key_namespace": "usa_wa_legislature_roster",
        "key_value": "danawhitfield:2015",
        "merged_into": None,
    },
    {"entity_id": "01B", "key_namespace": "wa_pdc", "key_value": "999", "merged_into": None},
    {
        "entity_id": "01C",
        "key_namespace": "usa_wa_legislature",
        "key_value": "5",
        "merged_into": "01A",
    },
]

SPONSORS = [
    {"biennium": "2023-24", "member_id": "27992", "name": "Dana W. Whitfield", "agency": "House"},
    {"biennium": "2025-26", "member_id": "27992", "name": "Dana Whitfield", "agency": "House"},
]
ROSTER = [
    {"year": 2015, "name": "Dana Whitfield", "district": 14, "chamber": "house"},
    {"year": 2025, "name": "Dana Whitfield-Lee", "district": 14, "chamber": "house"},
]
PDC = [{"person_id": "999", "filer_name": "DOE JANE", "election_year": 2024}]


def test_person_survivorship_roster_over_wsl_over_pdc(monkeypatch) -> None:

    monkeypatch.setattr(mod, "identity_fold", lambda name: "danawhitfield")
    rows = person_rows(CROSSWALK, sponsors=SPONSORS, roster=ROSTER, pdc=PDC)
    by_id = {r["entity_id"]: r for r in rows}
    # merged-away entities do not appear as conformed persons
    assert set(by_id) == {"01A", "01B"}
    # roster (latest revision-year name) wins over WSL
    assert by_id["01A"]["name_full"] == "Dana Whitfield-Lee"
    assert by_id["01A"]["name_source"] == "roster"
    # PDC-only person falls back to the (title-cased) filer name
    assert by_id["01B"]["name_source"] == "pdc"


def test_person_wsl_fallback_uses_latest_biennium() -> None:
    crosswalk = [c for c in CROSSWALK if c["key_namespace"] != "usa_wa_legislature_roster"]
    rows = person_rows(crosswalk, sponsors=SPONSORS, roster=[], pdc=PDC)
    by_id = {r["entity_id"]: r for r in rows}
    assert by_id["01A"]["name_full"] == "Dana Whitfield"
    assert by_id["01A"]["name_source"] == "wsl"


ORG_CROSSWALK = [
    {
        "entity_id": "02A",
        "key_namespace": "usa_wa_legislature",
        "key_value": "1754",
        "merged_into": None,
    },
]
COMMITTEES = [
    {
        "biennium": "2023-24",
        "committee_id": "1754",
        "agency": "House",
        "name": "Ag",
        "long_name": "Agriculture",
        "acronym": "AG",
        "phone": None,
    },
    {
        "biennium": "2025-26",
        "committee_id": "1754",
        "agency": "House",
        "name": "Ag & Water",
        "long_name": "Agriculture & Water",
        "acronym": "AGW",
        "phone": None,
    },
]


def test_org_rows_take_latest_biennium_attributes() -> None:
    rows = org_rows(ORG_CROSSWALK, committees=COMMITTEES, meetings=[])
    [row] = rows
    assert row["entity_id"] == "02A"
    assert row["name"] == "Ag & Water"
    assert row["long_name"] == "Agriculture & Water"
    assert row["agency"] == "House"
    assert row["first_biennium"] == "2023-24"
    assert row["last_biennium"] == "2025-26"


def test_org_rows_meeting_derived_fallback() -> None:
    crosswalk = [
        {
            "entity_id": "02B",
            "key_namespace": "usa_wa_legislature",
            "key_value": "-5",
            "merged_into": None,
        }
    ]
    meetings = [
        {
            "committee_id": "-5",
            "committee_agency": "Joint",
            "committee_name": "JLARC",
            "meeting_window": "2023-01-01:2024-12-31",
        }
    ]
    [row] = org_rows(crosswalk, committees=[], meetings=meetings)
    assert row["name"] == "JLARC"
    assert row["agency"] == "Joint"


def test_org_rows_structural_branch_uses_verbatim_vocabulary() -> None:
    """CR 31g: the chambers/legislature/parties take their names and types from
    STRUCTURAL_ORGS, not from any attested wire."""
    crosswalk = [
        {
            "entity_id": "03A",
            "key_namespace": "usa_wa_legislature",
            "key_value": "usa_wa_house",
            "merged_into": None,
        },
    ]
    [row] = org_rows(crosswalk, committees=COMMITTEES, meetings=[])
    assert row["name"] == "Washington State House of Representatives"
    assert row["org_type"] == "chamber"
    assert row["first_biennium"] is None


def test_org_rows_drop_tombstoned_entities() -> None:
    merged = [dict(ORG_CROSSWALK[0], merged_into="09Z")]
    assert org_rows(merged, committees=COMMITTEES, meetings=[]) == []
