"""C5 committee lineage-candidate curation assist (usa-wa#124) — pure ranking."""

from datetime import date

from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from usa_wa_adapter_legislature.committees.lifecycle import CommitteeWindow
from usa_wa_adapter_legislature.committees.lineage_suggest import (
    CandidateInfo,
    build_candidate_infos,
    name_similarity,
    significant_tokens,
    suggest_candidates,
)


def test_significant_tokens_drops_stopwords():
    toks = significant_tokens("Washington State Senate Labor and Commerce Committee")
    assert toks == {"labor", "commerce"}


def test_name_similarity_high_for_overlapping_names():
    a = significant_tokens("Senate Committee on Labor, Commerce & Consumer Protection")
    b = significant_tokens("Senate Committee on Labor and Commerce")
    assert name_similarity(a, b) > 0.3


def _info(sid, name, chamber, window=None, members=frozenset()):
    return CandidateInfo(
        source_id=sid, name=name, chamber_key=chamber, window=window, member_ids=members
    )


def _win(sid, *, founded=None, dissolved=None, current=False):
    return CommitteeWindow(
        source_id=sid, is_current=current, founded_year=founded, dissolved_year=dissolved
    )


def test_same_chamber_similar_names_suggested_with_window_direction():
    infos = [
        _info(
            "14294",
            "Labor, Commerce & Consumer Protection",
            "senate",
            _win("14294", founded=2015, dissolved=2020),
        ),
        _info("28244", "Labor and Commerce", "senate", _win("28244", founded=2021, current=True)),
    ]
    out = suggest_candidates(infos)
    assert len(out) == 1
    c = out[0]
    assert (c.predecessor_id, c.successor_id) == ("14294", "28244")  # dissolved feeds current
    assert c.direction_certain
    assert "adjacent_windows" in c.reasons  # 2020 -> 2021


def test_different_chamber_not_suggested():
    infos = [
        _info("A", "Labor and Commerce", "senate"),
        _info("B", "Labor and Commerce", "house"),  # same name, different chamber
    ]
    assert suggest_candidates(infos) == []


def test_dissimilar_names_not_suggested():
    infos = [
        _info("A", "Transportation", "senate"),
        _info("B", "Health & Long Term Care", "senate"),
    ]
    assert suggest_candidates(infos) == []


def test_shared_members_raise_score_and_are_reasoned():
    shared = frozenset({"p1", "p2", "p3"})
    infos = [
        _info("A", "Labor and Commerce", "senate", _win("A", founded=2015, dissolved=2020), shared),
        _info("B", "Labor Commerce", "senate", _win("B", founded=2021, current=True), shared),
    ]
    out = suggest_candidates(infos)
    assert any(r.startswith("shared_members=3") for r in out[0].reasons)


def test_direction_uncertain_when_no_window_order():
    infos = [
        _info("A", "Labor and Commerce", "senate"),  # no windows
        _info("B", "Labor Commerce", "senate"),
    ]
    out = suggest_candidates(infos)
    assert out and not out[0].direction_certain
    assert "direction_uncertain" in out[0].reasons


async def test_build_candidate_infos_reads_chamber_and_members(db_session, usa_wa):
    chamber = Organization(
        source="usa_wa_legislature", source_id="senate", name="Senate", org_type="chamber"
    )
    db_session.add(chamber)
    await db_session.flush()
    person = Person(source="usa_wa_legislature", source_id="p1", name_full="M")
    db_session.add(person)
    org = Organization(
        source="usa_wa_legislature",
        source_id="14294",
        name="Labor and Commerce",
        org_type="committee",
        parent_organization_id=chamber.id,
    )
    db_session.add(org)
    await db_session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id="member:14294",
        organization_id=org.id,
        name="member",
        role_type="committee_member",
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        Assignment(
            source="usa_wa_legislature",
            source_id="a1",
            role_id=role.id,
            person_id=person.id,
            valid_from=date(2015, 1, 1),
            is_active=True,
        )
    )
    await db_session.flush()

    infos = await build_candidate_infos(db_session, windows={})
    info = next(i for i in infos if i.source_id == "14294")
    assert info.chamber_key == str(chamber.id)
    assert info.member_ids == frozenset({str(person.id)})


def test_ranked_by_score_descending():
    infos = [
        _info("A", "Labor and Commerce", "senate", _win("A", dissolved=2020)),
        _info(
            "B",
            "Labor Commerce Workforce",
            "senate",
            _win("B", founded=2021, current=True),
            frozenset({"p1", "p2"}),
        ),
        _info("C", "Commerce Trade", "senate", _win("C", founded=2005, current=True)),
    ]
    out = suggest_candidates(infos)
    scores = [c.score for c in out]
    assert scores == sorted(scores, reverse=True)
