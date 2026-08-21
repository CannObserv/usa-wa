"""Tests for synthesis.py — pure functions producing anchor row dicts."""

from ulid import ULID as _ULID

from usa_wa_adapter_legislature.synthesis import (
    biennium_session,
    chamber_orgs,
    legislature_org,
    party_orgs,
    regular_sessions,
)
from usa_wa_common.parties import PARTY_SLUGS


def test_party_orgs_covers_the_whole_declared_vocabulary():
    """One Org row per ``PARTY_SLUGS`` entry (#228): the two majors plus the six
    historical minors PM seeded under ``org_wa_party`` (power-map#442/#443) — the org
    descriptor auto-attaches each by bare-slug identifier, so a missing local row is a
    party span with no Role anchor. Still no Independent (absence of an Assignment)."""
    jur = _ulid()
    rows = party_orgs(jur)
    assert {r["source_id"] for r in rows} == {f"party-{slug}" for slug in PARTY_SLUGS}
    assert all(r["org_type"] == "party" for r in rows)
    assert all(r["parent_organization_id"] is None for r in rows)
    assert all(r["jurisdiction_id"] == jur for r in rows)
    by_id = {r["source_id"]: r for r in rows}
    assert by_id["party-republican"]["name"] == "Washington State Republican Party"
    assert by_id["party-democratic"]["name"] == "Washington State Democratic Party"
    # PM's own display names (fetched live 2026-08-20), so identifier auto-attach
    # carries no divergent name evidence.
    assert by_id["party-peoples"]["name"] == "Washington State People's Party"
    assert by_id["party-socialist"]["name"] == "Socialist Party of Washington"


def _ulid() -> _ULID:
    return _ULID()


# ----- legislature_org -----


def test_legislature_org_produces_canonical_row_shape():
    """The legislature Org dict carries source/slug/type/jurisdiction binding."""
    jur_id = _ulid()
    org = legislature_org(jur_id)

    assert org["source"] == "usa_wa_legislature"
    assert org["source_id"] == "usa_wa_legislature"
    assert org["name"] == "Washington State Legislature"
    assert org["org_type"] == "legislature"
    assert org["jurisdiction_id"] == jur_id
    assert org["parent_organization_id"] is None


# ----- chamber_orgs -----


def test_chamber_orgs_returns_house_and_senate():
    """House first, Senate second; both child of the legislature Org."""
    leg_id, jur_id = _ulid(), _ulid()
    chambers = chamber_orgs(leg_id, jur_id)

    assert [c["short_name"] for c in chambers] == ["House", "Senate"]
    for c in chambers:
        assert c["source"] == "usa_wa_legislature"
        assert c["org_type"] == "chamber"
        assert c["parent_organization_id"] == leg_id
        assert c["jurisdiction_id"] == jur_id


def test_chamber_orgs_source_ids_stable_across_calls():
    """source_id is the deterministic stable handle used for upsert."""
    leg_id, jur_id = _ulid(), _ulid()
    a, b = chamber_orgs(leg_id, jur_id), chamber_orgs(leg_id, jur_id)
    assert [c["source_id"] for c in a] == [c["source_id"] for c in b]
    assert {c["source_id"] for c in a} == {"usa_wa_house", "usa_wa_senate"}


# ----- biennium_session -----


def test_biennium_session_carries_classification_and_organization():
    """The biennium row has ``classification='biennium'`` and the legislature Org."""
    leg_id = _ulid()
    sess = biennium_session(leg_id, "2025-26")

    assert sess["organization_id"] == leg_id
    assert sess["source"] == "usa_wa_legislature"
    assert sess["source_id"] == "biennium:2025-26"
    assert sess["slug"] == "usa-wa-2025-26"
    assert sess["classification"] == "biennium"
    assert sess["biennium_label"] == "2025-26"
    assert sess["parent_legislative_session_id"] is None


# ----- regular_sessions -----


def test_regular_sessions_emits_one_per_calendar_year():
    """Two rows for a single biennium — 2025 and 2026 regular sessions."""
    biennium_id, leg_id = _ulid(), _ulid()
    sessions = regular_sessions(biennium_id, leg_id, "2025-26")

    assert [s["slug"] for s in sessions] == ["usa-wa-2025", "usa-wa-2026"]
    for s in sessions:
        assert s["organization_id"] == leg_id
        assert s["classification"] == "regular"
        assert s["parent_legislative_session_id"] == biennium_id
        assert s["biennium_label"] == "2025-26"
        assert s["source"] == "usa_wa_legislature"
    assert [s["source_id"] for s in sessions] == ["session:2025", "session:2026"]
