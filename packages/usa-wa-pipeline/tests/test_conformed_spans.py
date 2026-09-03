"""Conformed tenure spans as a stateless transform (#309 part 2).

The span engine and every guard it carries are imported UNCHANGED from the
domain and the adapter (each encodes a prod incident — #267/#272/#119/#145,
the #105 roster hygiene, the #144 artifact denylist). What is new here is only
the plumbing: staging rows → the projectors' wire shape, and the same
orchestration ORDER the Postgres-tier builders use. These tests pin that
plumbing and the order, not the engine's own behavior.
"""

from datetime import date

import pytest

from clearinghouse_domain_legislative.span_kinds import (
    KIND_COMMITTEE,
    KIND_PARTY,
    KIND_SENATE,
)
from usa_wa_pipeline.conformed.spans import (
    SpanInputs,
    assignment_rows,
    build_all_spans,
    committee_rosters,
    entity_index,
    roster_records,
    sponsor_wire_rows,
)

BIENNIUM = "2023-24"
CURRENT = "2025-26"

#: Every test below builds a corpus with no roster tier, so each states the
#: deepening explicitly (CR 57): an EMPTY roster is the one thing the builder
#: refuses to guess at, because guessing publishes shallow spans silently.
NO_DEEPENING: list = []


def _sponsor(member_id: str, biennium: str, **over) -> dict:
    row = {
        "biennium": biennium,
        "member_id": member_id,
        "agency": "Senate",
        "name": "Dana Whitfield",
        "long_name": "Senator Whitfield",
        "first_name": "Dana",
        "last_name": "Whitfield",
        "party": "D",
        "district": "14",
    }
    row.update(over)
    return row


def _committee_member(member_id: str, biennium: str, committee_id: str = "500", **over) -> dict:
    row = {
        "biennium": biennium,
        "committee_id": committee_id,
        "committee_agency": "Senate",
        "committee_name": "Ways & Means",
        "member_id": member_id,
        "name": "Dana Whitfield",
        "long_name": "Senator Whitfield",
        "first_name": "Dana",
        "last_name": "Whitfield",
        "agency": "Senate",
        "party": "D",
        "district": "14",
    }
    row.update(over)
    return row


def test_sponsor_wire_rows_restores_the_projector_shape() -> None:
    """The projectors consume WSL wire dicts; staging carries the same facts
    under normalized names. The adapter must be exact — a dropped field is a
    silently missing observation."""
    wires = sponsor_wire_rows([_sponsor("100", BIENNIUM)])
    assert list(wires) == [BIENNIUM]
    [wire] = wires[BIENNIUM]
    assert wire["Id"] == "100"
    assert wire["FirstName"] == "Dana"
    assert wire["LastName"] == "Whitfield"
    assert wire["Party"] == "D"
    assert wire["Agency"] == "Senate"
    assert wire["District"] == "14"


def test_committee_rosters_key_on_biennium_and_committee() -> None:
    rosters = committee_rosters(
        [
            _committee_member("100", BIENNIUM, committee_id="500"),
            _committee_member("101", BIENNIUM, committee_id="500"),
            _committee_member("100", BIENNIUM, committee_id="600"),
        ]
    )
    assert set(rosters) == {(BIENNIUM, "500"), (BIENNIUM, "600")}
    assert [w["Id"] for w in rosters[(BIENNIUM, "500")]] == ["100", "101"]


def test_name_blanked_stub_yields_no_observation() -> None:
    """`is_person` screens the name-blanked stubs GetSponsors returns for a
    superseded tenure — reachable only because staging now carries the names."""
    inputs = SpanInputs(
        sponsors=[_sponsor("100", CURRENT, first_name="", last_name="")],
        committee_members=[],
        events=[],
    )
    assert build_all_spans(inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING) == []


def test_party_and_senate_spans_merge_contiguous_bienniums() -> None:
    inputs = SpanInputs(
        sponsors=[_sponsor("100", "2021-22"), _sponsor("100", "2023-24"), _sponsor("100", CURRENT)],
        committee_members=[],
        events=[],
    )
    spans = build_all_spans(inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING)
    by_kind = {s.kind: s for s in spans}
    assert set(by_kind) == {KIND_PARTY, KIND_SENATE}
    party = by_kind[KIND_PARTY]
    assert party.start_biennium == "2021-22"
    assert party.valid_from == date(2021, 1, 1)
    assert party.valid_to is None and party.is_active  # reaches the current biennium
    assert by_kind[KIND_SENATE].discriminator == "14"


def test_committee_spans_key_on_committee_id() -> None:
    inputs = SpanInputs(
        sponsors=[],
        committee_members=[
            _committee_member("100", "2021-22"),
            _committee_member("100", "2023-24"),
        ],
        events=[],
    )
    [span] = build_all_spans(inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING)
    assert span.kind == KIND_COMMITTEE
    assert span.discriminator == "500"
    assert span.start_biennium == "2021-22"
    assert span.valid_to == date(2024, 12, 31)  # closed: does not reach current
    assert not span.is_active


def test_independent_party_emits_no_party_span() -> None:
    """canonicalize_party folds independent/blank to None — the major-party-only
    rule the projector enforces."""
    inputs = SpanInputs(
        sponsors=[_sponsor("100", CURRENT, party="I", agency="House")],
        committee_members=[],
        events=[],
    )
    assert build_all_spans(inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING) == []


def test_house_row_emits_no_senate_seat_span() -> None:
    """House chamber tenure needs the PDC ballot Position (#79) — the sponsor
    projection deliberately emits a seat observation for Senate rows only."""
    inputs = SpanInputs(
        sponsors=[_sponsor("100", CURRENT, agency="House")],
        committee_members=[],
        events=[],
    )
    kinds = {
        s.kind
        for s in build_all_spans(inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING)
    }
    assert kinds == {KIND_PARTY}


def test_assignment_rows_join_the_crosswalk_and_count_drops() -> None:
    """Unregistered members drop cleanly (inner join) and are counted — the
    acceptance criterion in #309."""
    inputs = SpanInputs(
        sponsors=[_sponsor("100", CURRENT), _sponsor("999", CURRENT)],
        committee_members=[],
        events=[],
    )
    spans = build_all_spans(inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING)
    rows, counters = assignment_rows(spans, {"usa_wa_legislature:100": "01ENTITY"})
    assert {r["member_id"] for r in rows} == {"100"}
    assert all(r["entity_id"] == "01ENTITY" for r in rows)
    assert counters["unregistered_spans"] == 2  # 999's party + senate spans
    [party] = [r for r in rows if r["span_kind"] == KIND_PARTY]
    assert party["span_start_biennium"] == CURRENT
    assert party["span_discriminator"] == "democratic"
    assert party["is_active"] is True
    assert party["valid_to"] is None
    assert party["source"] == "usa_wa_legislature"


def test_entity_index_follows_merge_tombstones() -> None:
    """An assignment must follow a merge, not vanish with it: a key on a
    tombstoned entity resolves to the survivor (chains included)."""
    crosswalk = [
        {"natural_key": "usa_wa_legislature:1", "entity_id": "A", "merged_into": "B"},
        {"natural_key": "usa_wa_legislature:2", "entity_id": "B", "merged_into": "C"},
        {"natural_key": "usa_wa_legislature:3", "entity_id": "C", "merged_into": None},
    ]
    index = entity_index(crosswalk)
    assert index["usa_wa_legislature:1"] == "C"
    assert index["usa_wa_legislature:2"] == "C"
    assert index["usa_wa_legislature:3"] == "C"


def test_deepening_derived_from_an_empty_roster_is_refused() -> None:
    """CR 57: an empty roster tier silently re-asserts SHALLOW 1991-start spans.

    The #228 deepening is a standing input, not an optional enrichment: without
    it a member crossing the archive floor emits a 1991-start span abutting a
    roster-sourced twin (the #97 collapse). The publish shrink gate cannot see
    it — the key set changes while the row count barely moves — and the parity
    probe runs after publish. So the builder refuses rather than guessing.
    """
    inputs = SpanInputs(
        sponsors=[_sponsor("100", CURRENT)], committee_members=[], events=[], roster=[]
    )
    with pytest.raises(ValueError, match="roster"):
        build_all_spans(inputs, current_biennium=CURRENT)


def test_an_empty_corpus_needs_no_roster() -> None:
    """The refusal is about a roster that went missing under a live corpus, not
    about the hermetic build (empty raw root ⇒ no sponsors ⇒ nothing to deepen)."""
    assert (
        build_all_spans(SpanInputs(sponsors=[], committee_members=[]), current_biennium=CURRENT)
        == []
    )


def test_explicit_extras_are_always_honored() -> None:
    """`extra_observations` is the deliberate seam: passing it — even empty —
    states the deepening rather than deriving it."""
    inputs = SpanInputs(sponsors=[_sponsor("100", CURRENT)], committee_members=[], roster=[])
    spans = build_all_spans(inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING)
    assert {s.kind for s in spans} == {KIND_PARTY, KIND_SENATE}


def test_malformed_roster_rows_are_counted_not_silently_dropped(caplog) -> None:
    """CR 63: report-don't-drop. A staging regression that mangles roster rows
    must leave a trace, not vanish into a bare `continue`."""
    good = {
        "district": "5",
        "chamber": "house",
        "year": "1975",
        "order": "1",
        "name": "Jordan Smith",
        "party_token": "R",
        "annotation": None,
    }
    records = roster_records([good, {**good, "year": "not-a-year"}, {"district": "5"}])
    assert len(records) == 1
    assert "roster_records_malformed" in caplog.text
