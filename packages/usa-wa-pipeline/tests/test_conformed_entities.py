"""Conformed persons/orgs survivorship (#309): registry ⨝ staging, pure."""

import pandas as pd

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
    """A tombstoned id is never published under its own ULID.

    #366 narrowed what this test may assert. It used to demand the row vanish
    ENTIRELY, which encoded the drop that cost Denny Heck his name: the keys go
    to the survivor now, so a merge whose survivor is otherwise keyless
    publishes under the survivor's id. What the guard is actually for — that
    the retired id stops being addressable — is unchanged, and stated directly.
    """
    merged = [dict(ORG_CROSSWALK[0], merged_into="09Z")]
    rows = org_rows(merged, committees=COMMITTEES, meetings=[])
    assert [r["entity_id"] for r in rows] == ["09Z"]
    assert not [r for r in rows if r["entity_id"] == "02A"]


# --- #364: a blank is not a name -------------------------------------------
#
# `GetSponsors` returns a name-blanked STUB for a superseded / departed (member,
# chamber-tenure): a real `Id`, `Name` a single space, no first/last, no
# district, no party. The canonical path has always screened those
# (`normalize.members.is_person`); the conformed survivorship did not, so four
# sitting legislators published `' '` as their legal name — found downstream by
# power-map#497, not here.

STUB = {
    "biennium": "2027-28",
    "member_id": "27992",
    "name": " ",
    "first_name": None,
    "last_name": None,
    "agency": "House",
}


def test_a_name_blanked_wsl_stub_never_wins_the_name() -> None:
    """The departed-member stub is the LATEST attestation and still loses.

    Newest-attestation-wins is a rule about names; a stub carries none, so it
    is not an attestation to be newest among. This is Tim Sheldon's shape
    exactly — real names through 2021-22, a blank in 2023-24 — and Robert
    Sutherland's and Simon Sefzik's; all three published as `' '`.
    """
    crosswalk = [c for c in CROSSWALK if c["key_namespace"] != "usa_wa_legislature_roster"]
    rows = person_rows(crosswalk, sponsors=[*SPONSORS, STUB], roster=[], pdc=PDC)
    by_id = {r["entity_id"]: r for r in rows}
    assert by_id["01A"]["name_full"] == "Dana Whitfield"
    assert by_id["01A"]["name_source"] == "wsl"


def test_a_blank_is_absent_not_a_name() -> None:
    """With nothing but stubs, the person has NO name — null, never `' '`.

    Null is the honest reading and the one the #490 contract needs: a consumer
    applying a producer-owned legal name naively must not be handed whitespace.
    """
    crosswalk = [c for c in CROSSWALK if c["key_namespace"] == "usa_wa_legislature"]
    [row] = person_rows(crosswalk, sponsors=[STUB], roster=[], pdc=[])
    assert row["name_full"] is None
    assert row["name_source"] is None


def test_a_blank_roster_or_pdc_name_falls_through_to_the_next_source() -> None:
    """Survivorship skips a blank rather than stopping at it.

    The bug was WSL's, but precedence is a chain: a blank winning at any link
    would publish whitespace just the same, so all three clean identically.
    """
    roster = [{"year": 2025, "name": "  ", "district": 14, "chamber": "house"}]
    pdc = [{"person_id": "999", "filer_name": " ", "election_year": 2024}]
    rows = person_rows(CROSSWALK, sponsors=SPONSORS, roster=roster, pdc=pdc)
    by_id = {r["entity_id"]: r for r in rows}
    assert by_id["01A"]["name_source"] == "wsl"  # roster blank → next link
    assert by_id["01B"]["name_full"] is None  # PDC blank → no name at all


def test_a_pandas_null_is_absent_not_the_string_nan() -> None:
    """CR 1: `person_rows` reads pandas records, where a null in a non-object
    column arrives as float NaN. `str(nan)` is `'nan'` — a name, as far as
    everything downstream is concerned, and exactly the silent-wrong-name shape
    #364 exists to end. Today's name columns are VARCHAR so this cannot fire;
    what it must never do is fire QUIETLY if that changes."""
    crosswalk = [c for c in CROSSWALK if c["key_namespace"] == "usa_wa_legislature"]
    # CR 9: all three of pandas' nulls, not just the float one. `pd.NA` broke the
    # first cut of this guard outright — `pd.NA != pd.NA` is `pd.NA`, and its
    # truth value RAISES — so a fix aimed at one silent wrong name had bought an
    # uncaught abort of the whole nightly build in its place.
    for null in (float("nan"), pd.NA, pd.NaT):
        sponsors = [dict(STUB, name=null, biennium="2029-30")]
        [row] = person_rows(crosswalk, sponsors=sponsors, roster=[], pdc=[])
        assert row["name_full"] is None, null


def test_a_published_name_is_trimmed(monkeypatch) -> None:
    """Real names arrive with trailing whitespace too — `'Marlo Braun '` from
    WSL, `'MICHAEL JAMES BAUMGARTNER '` from PDC. The stored name is the name."""
    monkeypatch.setattr(mod, "identity_fold", lambda name: "danawhitfield")
    roster = [{"year": 2025, "name": " Dana Whitfield-Lee ", "district": 14, "chamber": "house"}]
    pdc = [{"person_id": "999", "filer_name": "DOE JANE ", "election_year": 2024}]
    by_id = {r["entity_id"]: r for r in person_rows(CROSSWALK, sponsors=[], roster=roster, pdc=pdc)}
    assert by_id["01A"]["name_full"] == "Dana Whitfield-Lee"
    assert by_id["01B"]["name_full"] == "Doe Jane"


def test_org_names_get_the_same_blank_screen(monkeypatch) -> None:
    """CR 5: the same defect class one function over. A committee wire has never
    answered blank — measured zero across `stg_wsl_committees` and
    `stg_wsl_meetings` on the 2026-09-10 archive — but `organizations.name` is
    published under the same producer-owns-the-name contract as a person's, and
    nothing screened it. A no-op on today's corpus, by construction."""
    committees = [
        dict(COMMITTEES[0], biennium="2025-26", name=" ", long_name=" Agriculture ", acronym="AG  ")
    ]
    [row] = org_rows(ORG_CROSSWALK, committees=committees, meetings=[])
    assert row["name"] is None
    assert row["long_name"] == "Agriculture"
    # the acronym is deliberately NOT screened — CR 8, held
    assert row["acronym"] == "AG  "

    meetings = [{"committee_id": "-5", "committee_agency": "Joint", "committee_name": "  "}]
    crosswalk = [dict(ORG_CROSSWALK[0], entity_id="02B", key_value="-5")]
    [ref_row] = org_rows(crosswalk, committees=[], meetings=meetings)
    assert ref_row["name"] is None


# --- #366: a merge re-points, it does not delete ----------------------------
#
# The published crosswalk's tombstone is its only re-point signal, and two of
# the three conformed consumers already say so out loud: `spans.entity_index`
# ("an assignment must follow a merge rather than vanish with it") and
# `citations._key_index` both resolve to the survivor. `_live_entities` alone
# dropped the loser's rows, so the FIRST real merge (Denny Heck, #366) moved his
# 1977-85 party span and his roster citation onto the survivor and left the name
# behind — `persons` went from publishing "Dennis L. Heck" on one entity to
# publishing no name at all.

MERGED_CROSSWALK = [
    {
        "entity_id": "01WSL",
        "key_namespace": "usa_wa_legislature",
        "key_value": "31656",
        "merged_into": None,
    },
    {
        "entity_id": "01ROSTER",
        "key_namespace": "usa_wa_legislature_roster",
        "key_value": "danawhitfield:1977",
        "merged_into": "01WSL",
    },
]


def test_a_merged_entitys_keys_belong_to_the_survivor(monkeypatch) -> None:
    """The survivor is named by the loser's roster key, not left nameless."""
    monkeypatch.setattr(mod, "identity_fold", lambda name: "danawhitfield")
    roster = [{"year": 1977, "name": "Dana Whitfield", "district": 17, "chamber": "house"}]
    rows = person_rows(MERGED_CROSSWALK, sponsors=[], roster=roster, pdc=[])
    assert [r["entity_id"] for r in rows] == ["01WSL"]
    assert rows[0]["name_full"] == "Dana Whitfield"
    assert rows[0]["name_source"] == "roster"


def test_a_merge_chain_resolves_to_the_last_survivor(monkeypatch) -> None:
    """A→B→C: the name lands on C. The merge verb refuses a tombstoned survivor
    so a cycle cannot arise, but the walk is bounded the way the other two
    consumers bound theirs."""
    monkeypatch.setattr(mod, "identity_fold", lambda name: "danawhitfield")
    # CR 13: `merged_into` is a property of the ENTITY, so every row of an
    # entity carries the same value — `registry_read` cannot emit `01WSL` as
    # live on one row and tombstoned on another. The first cut of this test did
    # exactly that, and a chain test built on input the registry cannot produce
    # is not evidence that chains resolve.
    chain = [
        {**MERGED_CROSSWALK[0], "merged_into": "01FINAL"},
        MERGED_CROSSWALK[1],
        {
            "entity_id": "01FINAL",
            "key_namespace": "usa_wa_legislature",
            "key_value": "999",
            "merged_into": None,
        },
    ]
    roster = [{"year": 1977, "name": "Dana Whitfield", "district": 17, "chamber": "house"}]
    rows = person_rows(chain, sponsors=[], roster=roster, pdc=[])
    assert [r["entity_id"] for r in rows] == ["01FINAL"]
    assert rows[0]["name_full"] == "Dana Whitfield"


def test_org_rows_follow_a_merge_too() -> None:
    """Same function, same rule: a merged committee's attributes are the
    survivor's."""
    crosswalk = [
        {
            "entity_id": "02NEW",
            "key_namespace": "usa_wa_legislature",
            "key_value": "9999",
            "merged_into": None,
        },
        dict(ORG_CROSSWALK[0], merged_into="02NEW"),
    ]
    [row] = org_rows(crosswalk, committees=COMMITTEES, meetings=[])
    assert row["entity_id"] == "02NEW"
    assert row["name"] == "Ag & Water"


# --- published names carry no tenure annotation (usa-wa#378) ------------------

KREIDLER_NOTE = (
    "Myron “Mike” Kreidler (On leave of absence for military duty Jan. 8, 1991 to April 18, 1991)"
)


def test_a_roster_tenure_note_is_not_published_as_a_name() -> None:
    """The roster prints service facts inside the name column; `name_full` is a name.

    #378's prerequisite. This row's entity is one of the 17 roster/member-id
    duplicates, and merging it makes the ROSTER name win survivorship — so
    without this screen a leave-of-absence note becomes the published legal name
    of a live, power-map-resolved legislator. That is the #364 shape exactly, and
    power-map#497 is how #364 was found: downstream, in a consumer.

    The fold is asserted alongside on purpose. The note is stripped for
    *publication* only — `identity_fold` already ignored it, so the row is still
    located by the same registry key and no identity moves. A screen that
    changed the fold would re-key the person, which is the one thing this must
    not do.
    """
    crosswalk = [
        {
            "entity_id": "01K",
            "key_namespace": "usa_wa_legislature_roster",
            "key_value": "myronkreidler:1977",
            "merged_into": None,
        }
    ]
    roster = [{"year": 1977, "name": KREIDLER_NOTE, "district": 2, "chamber": "house"}]

    [row] = person_rows(crosswalk, sponsors=[], roster=roster, pdc=[])

    assert row["name_full"] == "Myron “Mike” Kreidler"
    assert row["name_source"] == "roster"


def test_a_marital_print_form_is_left_alone() -> None:
    """Whether she is published under her husband's name is an editorial call
    (#378 follow-on 2), so the screen does not quietly make it. Pinned here
    because the tempting fix — reusing `strip_non_name_parts` — would answer it
    by accident, and answer it wrong: it yields `Irwin LeCocq` for Mary."""
    crosswalk = [
        {
            "entity_id": "01R",
            "key_namespace": "usa_wa_legislature_roster",
            "key_value": "bellereeves:1923",
            "merged_into": None,
        }
    ]
    roster = [
        {"year": 1923, "name": "Belle (Mrs. Frank) Reeves", "district": 1, "chamber": "house"}
    ]

    [row] = person_rows(crosswalk, sponsors=[], roster=roster, pdc=[])

    assert row["name_full"] == "Belle (Mrs. Frank) Reeves"


# --- a name that is ALL annotation is absent, not blank (CR 151) --------------

ALL_NOTE = "(Resgnd Dec. 31, 1982)"


def test_an_all_annotation_roster_name_publishes_no_name() -> None:
    """Stripping must not turn a name into `''` — #364's shape, not a fix for it.

    Reachable, not theoretical: `identity_fold(ALL_NOTE)` is `''`, so a roster
    key of `:<year>` selects this row and publishes `name_full=''` with
    `name_source='roster'` — which `dbt/tests/persons_named.sql` fails the
    nightly build on. The roster branch is the one source whose selection does
    not guard on truthiness (`if fold in roster_latest` then takes `[1]`), so it
    is where an emptied name actually lands.
    """
    crosswalk = [
        {
            "entity_id": "01Z",
            "key_namespace": "usa_wa_legislature_roster",
            "key_value": ":1900",
            "merged_into": None,
        }
    ]
    roster = [{"year": 1900, "name": ALL_NOTE, "district": 1, "chamber": "house"}]

    [row] = person_rows(crosswalk, sponsors=[], roster=roster, pdc=[])

    assert row["name_full"] is None
    assert row["name_source"] is None


def test_display_name_returns_absent_not_blank() -> None:
    """`_display_name` extends `_name`, so it owes `_name`'s contract: a name or
    None, never `''`. The WSL and PDC call sites happen to guard on truthiness
    and so survive a blank today; that is incidental, and a future site written
    as `is not None` would republish #364."""
    assert mod._display_name(ALL_NOTE) is None
    assert mod._display_name("   ") is None
    assert mod._display_name(None) is None
    assert mod._display_name("Patty Murray") == "Patty Murray"
