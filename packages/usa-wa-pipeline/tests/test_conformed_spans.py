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
    ROSTER_SOURCE,
    SOURCE,
    OracleViolation,
    RosterResolution,
    SpanInputs,
    assignment_rows,
    build_all_spans,
    build_roster_spans,
    entity_index,
    roster_records,
    roster_resolution,
)
from usa_wa_pipeline.conformed.wire import committee_rosters, sponsor_wire_rows

BIENNIUM = "2023-24"
CURRENT = "2025-26"

#: Every test below builds a corpus with no roster tier and no ballot
#: archive, so each states both families explicitly (CR 57): an EMPTY input is
#: the one thing the builder refuses to guess at, because guessing publishes
#: silently-wrong spans. `usa_wa_pipeline.conformed.house` has its own tests.
NO_DEEPENING: list = []
NO_HOUSE: list = []


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
    assert (
        build_all_spans(
            inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING, house_spans=NO_HOUSE
        )
        == []
    )


def test_party_and_senate_spans_merge_contiguous_bienniums() -> None:
    inputs = SpanInputs(
        sponsors=[_sponsor("100", "2021-22"), _sponsor("100", "2023-24"), _sponsor("100", CURRENT)],
        committee_members=[],
        events=[],
    )
    spans = build_all_spans(
        inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING, house_spans=NO_HOUSE
    )
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
    [span] = build_all_spans(
        inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING, house_spans=NO_HOUSE
    )
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
    assert (
        build_all_spans(
            inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING, house_spans=NO_HOUSE
        )
        == []
    )


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
        for s in build_all_spans(
            inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING, house_spans=NO_HOUSE
        )
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
    spans = build_all_spans(
        inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING, house_spans=NO_HOUSE
    )
    rows, counters = assignment_rows({SOURCE: spans}, {f"{SOURCE}:100": "01ENTITY"})
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


def test_an_empty_roster_is_refused_at_the_resolve_the_models_call() -> None:
    """CR 76: the CR-57 refusal lived on a door production never opens.

    `build_all_spans` raises only when `extra_observations is None`, but BOTH
    callers — the `assignments` model and `parity_spans` — pass
    `roster_resolution(...).joined` so the ~8,600-record resolve runs once for
    two families. `roster_resolution` returned an empty resolution for an empty
    roster without complaint, so the exact combination CR 57 refuses reached a
    silent shallow publish through the only path that runs in production.

    The refusal therefore belongs on the resolve, which is the single door.
    """
    with pytest.raises(ValueError, match="roster"):
        roster_resolution([], [_sponsor("100", CURRENT)])


def test_the_models_call_shape_refuses_an_empty_roster() -> None:
    """The same defect stated end-to-end, in the shape the binder actually uses
    — resolve, then hand both halves to the builders. Pinning the call shape and
    not just the callee keeps a future refactor from re-opening the door by
    moving the resolve rather than by removing the guard."""
    sponsors = [_sponsor("100", CURRENT)]
    with pytest.raises(ValueError, match="roster"):
        resolution = roster_resolution([], sponsors)
        build_all_spans(
            SpanInputs(sponsors=sponsors, committee_members=[], roster=[], sos_results=[]),
            current_biennium=CURRENT,
            extra_observations=resolution.joined,
            house_spans=NO_HOUSE,
        )


def test_an_empty_corpus_resolves_to_nothing_without_complaint() -> None:
    """The complement of CR 76: with no sponsors there is nothing to deepen, so
    the hermetic build (`USA_WA_PIPELINE_HERMETIC=1`, empty raw root) must still
    resolve to an empty partition rather than raise."""
    assert roster_resolution([], []) == RosterResolution(joined=[], minted=[], records=[])


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
    spans = build_all_spans(
        inputs, current_biennium=CURRENT, extra_observations=NO_DEEPENING, house_spans=NO_HOUSE
    )
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


def test_a_roster_that_parses_to_nothing_is_refused(caplog) -> None:
    """CR 67: the round-4 refusal read the RAW rows, but `deepening_observations`
    returns [] on its own when no row survives parsing — so a roster tier that
    is present but entirely malformed reached the same silent shallow publish
    the guard exists to prevent. A staging rename is the plausible trigger,
    which is the scenario the guard was written for.
    """
    mangled = [
        {
            "district": "5",
            "chamber": "house",
            "year": "1975",
            "seat_order": 1,  # `order` renamed upstream
            "name": "Jordan Smith",
            "party_token": "R",
            "annotation": None,
        }
    ]
    inputs = SpanInputs(sponsors=[_sponsor("100", CURRENT)], committee_members=[], roster=mangled)
    with pytest.raises(ValueError, match="parsed"):
        build_all_spans(inputs, current_biennium=CURRENT)


def test_a_roster_that_parses_is_not_refused() -> None:
    """The complement: a roster that yields records builds, deepening derived."""
    good = {
        "district": "5",
        "chamber": "house",
        "year": "1975",
        "order": "1",
        "name": "Jordan Smith",
        "party_token": "R",
        "annotation": None,
    }
    inputs = SpanInputs(sponsors=[_sponsor("100", CURRENT)], committee_members=[], roster=[good])
    spans = build_all_spans(inputs, current_biennium=CURRENT, house_spans=NO_HOUSE)
    assert {s.kind for s in spans} == {
        KIND_PARTY,
        KIND_SENATE,
    }


def _roster(name: str, year: int, district: int = 5, chamber: str = "house", **over) -> dict:
    row = {
        "district": str(district),
        "chamber": chamber,
        "year": str(year),
        "order": "1",
        "name": name,
        "party_token": "R",
        "annotation": None,
    }
    row.update(over)
    return row


def test_roster_resolution_partitions_joined_from_minted() -> None:
    """One resolve, two families (#309 part 2 increment 2). The pre-1991 corpus
    splits by disposition: WSL-JOINED identities feed the #228 deepening of the
    WSL family, MINTED ones are the roster family's own spans. Resolving twice
    would be both wasteful and a chance for the halves to disagree.
    """
    resolution = roster_resolution(
        [_roster("Wilbur Cranston", 1925), _roster("Wilbur Cranston", 1927)],
        [_sponsor("100", CURRENT)],
    )
    assert resolution.minted, "a pre-1991 stranger to the sponsor corpus mints"
    assert not resolution.joined
    assert {o.member_id for o in resolution.minted} == {"wilburcranston:1925"}


def test_roster_spans_key_on_the_minted_identity() -> None:
    """The roster family's member_id is `<fold>:<first-session-year>` — which is
    why an assignment source_id carries FIVE colon segments there (CR 58)."""
    resolution = roster_resolution(
        [_roster("Wilbur Cranston", 1925), _roster("Wilbur Cranston", 1927)], []
    )
    spans = build_roster_spans(resolution, events=[], current_biennium=CURRENT)
    assert spans
    for span in spans:
        assert span.member_id == "wilburcranston:1925"
        assert span.kind in {KIND_PARTY, KIND_SENATE}
        assert not span.is_active, "every pre-1991 span is closed"
        assert len(span.source_id.split(":")) == 5


def test_senate_roster_rows_emit_a_seat_span() -> None:
    resolution = roster_resolution(
        [_roster("Wilbur Cranston", 1925, district=30, chamber="senate")], []
    )
    spans = build_roster_spans(resolution, events=[], current_biennium=CURRENT)
    by_kind = {s.kind: s for s in spans}
    assert set(by_kind) == {KIND_PARTY, KIND_SENATE}
    assert by_kind[KIND_SENATE].discriminator == "30"


def test_an_unknown_party_token_is_refused() -> None:
    """The projector's party vocabulary is an oracle, not a best effort: a new
    edition introducing an unclassified abbreviation must abort rather than
    publish a member with no party."""
    with pytest.raises(OracleViolation, match="party"):
        roster_resolution([_roster("Wilbur Cranston", 1925, party_token="ZZZ")], [])


def test_assignment_rows_tag_each_family_with_its_own_source() -> None:
    """Both families land in one `assignments` table, so each row must name the
    source its natural key belongs to — the crosswalk lookup is
    `<source>:<member_id>` and the two spaces are disjoint."""
    wsl = build_all_spans(
        SpanInputs(sponsors=[_sponsor("100", CURRENT)], committee_members=[]),
        current_biennium=CURRENT,
        extra_observations=NO_DEEPENING,
        house_spans=NO_HOUSE,
    )
    resolution = roster_resolution([_roster("Wilbur Cranston", 1925)], [])
    roster_spans = build_roster_spans(resolution, events=[], current_biennium=CURRENT)
    rows, counters = assignment_rows(
        {SOURCE: wsl, ROSTER_SOURCE: roster_spans},
        {
            f"{SOURCE}:100": "01WSLENTITY",
            f"{ROSTER_SOURCE}:wilburcranston:1925": "01ROSTERENTITY",
        },
    )
    by_source = {r["source"] for r in rows}
    assert by_source == {SOURCE, ROSTER_SOURCE}
    assert {r["entity_id"] for r in rows if r["source"] == ROSTER_SOURCE} == {"01ROSTERENTITY"}
    assert counters["unregistered_spans"] == 0
    assert counters["published"] == len(rows) == len(wsl) + len(roster_spans)
