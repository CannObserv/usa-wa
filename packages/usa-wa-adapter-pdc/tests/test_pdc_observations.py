"""Pure House-position observation projector (#79) — winners → tenure observations.

The archive-first Phase B (#79) projects each year's PDC winner cohort into
:class:`~usa_wa_adapter_legislature.tenure_spans.Observation`s (which the span builder merges
across years) plus the ``person_wa_pdc`` identifier links — reusing the #69/#74 match + mover
inference, but era-matched (the caller pairs each cohort with its seating biennium's roster)
and emitting observations instead of per-biennium Assignments. Pure: no DB, no session.
"""

from __future__ import annotations

from usa_wa_adapter_pdc.normalize.pdc_matching import build_house_roster, build_senate_roster
from usa_wa_adapter_pdc.normalize.pdc_observations import (
    KIND_HOUSE,
    build_house_position_observations,
    build_senate_identity_links,
)

BIENNIUM = "2013-14"


def _winner(pdc_id, ld, position, filer, party="DEMOCRAT"):
    return {
        "person_id": pdc_id,
        "legislative_district": str(ld),
        "position": str(position),
        "filer_name": filer,
        "party_code": party,
    }


def _sponsor(mid, ld, last, agency="House", party="D"):
    return {
        "Id": mid,
        "FirstName": "X",
        "LastName": last,
        "District": str(ld),
        "Agency": agency,
        "Party": party,
    }


def test_direct_match_emits_observation_and_identifier():
    house = build_house_roster([_sponsor(100, 5, "Rivers"), _sponsor(200, 5, "Barkis", party="R")])
    proj = build_house_position_observations(
        [_winner("900", 5, 1, "Ann Rivers")],
        house_roster=house,
        senate_roster={},
        biennium=BIENNIUM,
    )

    assert len(proj.observations) == 1
    obs = proj.observations[0]
    assert obs.member_id == "100"
    assert obs.kind == KIND_HOUSE
    assert obs.discriminator == "ld-5-position-1"
    assert obs.biennium == BIENNIUM
    assert proj.pdc_identifiers == [("100", "900")]
    assert proj.summary["direct_seated"] == 1


def test_position_2_distinct_discriminator():
    house = build_house_roster([_sponsor(100, 5, "Rivers"), _sponsor(200, 5, "Barkis", party="R")])
    proj = build_house_position_observations(
        [
            _winner("900", 5, 1, "Ann Rivers"),
            _winner("901", 5, 2, "Andrew Barkis", party="REPUBLICAN"),
        ],
        house_roster=house,
        senate_roster={},
        biennium=BIENNIUM,
    )
    discs = {o.discriminator for o in proj.observations}
    assert discs == {"ld-5-position-1", "ld-5-position-2"}


def test_unmatched_winner_without_mover_signal_is_unresolved():
    proj = build_house_position_observations(
        [_winner("900", 5, 1, "Ghost Candidate")],
        house_roster={},  # nobody to match
        senate_roster={},
        biennium=BIENNIUM,
    )
    assert proj.observations == []
    assert proj.pdc_identifiers == []
    assert proj.summary["unresolved"] == 1


def test_historical_mid_biennium_mover_infers_seat_and_cross_links():
    """LD5: Rep won Pos 1 then moved to the Senate mid-biennium (blanked House stub → absent).
    An appointed replacement holds the seat. The deferred winner reappears as LD5's Senator →
    the seat is inferred for the replacement, and the mover's PDC id cross-links to the Senate
    Person (#74, applied historically)."""
    # House roster: only the appointed replacement (300) — the mover is gone (blanked stub).
    house = build_house_roster([_sponsor(300, 5, "Replacement")])
    # Senate roster: the mover now sits as LD5's Senator (member 100).
    senate = build_senate_roster([_sponsor(100, 5, "Rivers", agency="Senate")])
    proj = build_house_position_observations(
        [_winner("900", 5, 1, "Ann Rivers")],  # the mover's original House winner row
        house_roster=house,
        senate_roster=senate,
        biennium=BIENNIUM,
    )

    # inferred seat for the replacement (no pdc id on the seat)
    assert len(proj.observations) == 1
    assert proj.observations[0].member_id == "300"
    assert proj.observations[0].discriminator == "ld-5-position-1"
    assert ("300", "900") not in proj.pdc_identifiers  # inferred seat carries no pdc id
    # mover's PDC identity cross-links onto their Senate Person (member 100)
    assert ("100", "900") in proj.pdc_identifiers
    assert proj.summary["inferred_seated"] == 1
    assert proj.summary["movers_linked"] == 1
    assert ("300", BIENNIUM) in proj.inferred_keys


def test_historical_mover_inference_resolves_position_via_fallback():
    """Pre-2018 (position-less) analog of the mover-inference test: the deferred winner has no
    PDC position, so the inferred replacement's seat position comes from the #100 fallback keyed
    on the *inferred* member's folded surname (phase 2)."""
    # House roster: only the appointed replacement (300); the mover is gone (blanked stub).
    house = build_house_roster([_sponsor(300, 5, "Replacement")])
    senate = build_senate_roster([_sponsor(100, 5, "Rivers", agency="Senate")])
    row = _winner("900", 5, 1, "Ann Rivers")
    row["position"] = ""  # pre-2018: PDC omitted the position

    def fallback(ld, folded_last, party_slug):
        assert (ld, folded_last) == (5, "replacement")
        return "Position 2"

    proj = build_house_position_observations(
        [row],
        house_roster=house,
        senate_roster=senate,
        biennium=BIENNIUM,
        position_fallback=fallback,
    )
    assert [o.discriminator for o in proj.observations] == ["ld-5-position-2"]
    assert proj.observations[0].member_id == "300"
    assert ("100", "900") in proj.pdc_identifiers  # mover cross-link still emitted
    assert proj.summary["inferred_seated"] == 1
    assert proj.summary["movers_linked"] == 1


def test_historical_mover_inference_without_position_is_unresolved():
    """Same shape, but the fallback can't position the inferred member → no guess: the seat is
    left unresolved rather than emitted position-less."""
    house = build_house_roster([_sponsor(300, 5, "Replacement")])
    senate = build_senate_roster([_sponsor(100, 5, "Rivers", agency="Senate")])
    row = _winner("900", 5, 1, "Ann Rivers")
    row["position"] = ""

    proj = build_house_position_observations(
        [row],
        house_roster=house,
        senate_roster=senate,
        biennium=BIENNIUM,
        position_fallback=lambda ld, last, party: None,
    )
    assert proj.observations == []
    assert proj.summary["inferred_seated"] == 0
    assert proj.summary["unresolved"] == 1
    assert ("100", "900") in proj.pdc_identifiers  # the mover link is still valid


def test_position_absent_without_fallback_is_incomplete():
    """A pre-2018 winner row (no ``position``) with no fallback is counted ``incomplete`` and
    never matched — the unchanged 2018+ PDC-only path."""
    house = build_house_roster([_sponsor(100, 5, "Rivers")])
    row = _winner("900", 5, 1, "Ann Rivers")
    row["position"] = ""  # PDC omitted position (pre-2018 dataset shape)
    proj = build_house_position_observations(
        [row], house_roster=house, senate_roster={}, biennium=BIENNIUM
    )
    assert proj.observations == []
    assert proj.summary["incomplete"] == 1
    assert proj.summary["missing_position"] == 0


def test_position_absent_resolved_via_fallback_seats_member():
    """With a #100 fallback, a position-less winner that matches a WSL member is seated at the
    position the fallback supplies (keyed on the member's folded surname + party)."""
    house = build_house_roster([_sponsor(100, 5, "Rivers", party="R")])
    row = _winner("900", 5, 1, "Ann Rivers", party="REPUBLICAN")
    row["position"] = ""

    def fallback(ld, folded_last, party_slug):
        assert (ld, folded_last, party_slug) == (5, "rivers", "republican")
        return "Position 2"

    proj = build_house_position_observations(
        [row], house_roster=house, senate_roster={}, biennium=BIENNIUM, position_fallback=fallback
    )
    assert [o.discriminator for o in proj.observations] == ["ld-5-position-2"]
    assert proj.pdc_identifiers == [("100", "900")]
    assert proj.summary["direct_seated"] == 1


def test_fallback_returns_none_counts_missing_position():
    """A matched member the fallback can't position (SOS gap) is counted ``missing_position``,
    not silently emitted with a wrong seat."""
    house = build_house_roster([_sponsor(100, 5, "Rivers")])
    row = _winner("900", 5, 1, "Ann Rivers")
    row["position"] = ""
    proj = build_house_position_observations(
        [row],
        house_roster=house,
        senate_roster={},
        biennium=BIENNIUM,
        position_fallback=lambda ld, last, party: None,
    )
    assert proj.observations == []
    assert proj.summary["missing_position"] == 1
    assert proj.summary["direct_seated"] == 0


def test_pdc_position_takes_precedence_over_fallback():
    """When PDC carries a position, the fallback is not consulted (2018+ stays authoritative)."""
    house = build_house_roster([_sponsor(100, 5, "Rivers")])

    def fallback(ld, last, party):  # pragma: no cover - must not be called
        raise AssertionError("fallback consulted despite a PDC position")

    proj = build_house_position_observations(
        [_winner("900", 5, 1, "Ann Rivers")],
        house_roster=house,
        senate_roster={},
        biennium=BIENNIUM,
        position_fallback=fallback,
    )
    assert [o.discriminator for o in proj.observations] == ["ld-5-position-1"]


def test_double_match_same_member_skips_the_duplicate():
    """Two winner rows in one LD resolving to the *same* House member (e.g. a stray duplicate
    filer row) → the member is seated once; the second match is skipped, not double-emitted."""
    house = build_house_roster([_sponsor(100, 5, "Rivers")])
    proj = build_house_position_observations(
        [_winner("900", 5, 1, "Ann Rivers"), _winner("901", 5, 2, "Ann Rivers")],
        house_roster=house,
        senate_roster={},
        biennium=BIENNIUM,
    )
    assert len(proj.observations) == 1
    assert proj.observations[0].member_id == "100"
    assert proj.summary["direct_seated"] == 1


def test_incomplete_row_counted_not_emitted():
    proj = build_house_position_observations(
        [{"person_id": "", "legislative_district": "5", "position": "1", "filer_name": "X"}],
        house_roster={},
        senate_roster={},
        biennium=BIENNIUM,
    )
    assert proj.observations == []
    assert proj.summary["incomplete"] == 1


# --- Senate identity links (#75) — the identifier-only contribution + robustness tallies ------


def test_senate_link_matched():
    senate = build_senate_roster([_sponsor(100, 1, "Stanford", agency="Senate")])
    links = build_senate_identity_links(
        [_winner("800", 1, 0, "Derek Stanford")], senate_roster=senate
    )
    assert links.identifiers == [("100", "800")]
    assert links.summary == {"winners": 1, "matched": 1, "unresolved": 0, "incomplete": 0}


def test_senate_link_incomplete_when_district_missing():
    """A winner missing person_id or district is counted ``incomplete``, never matched."""
    senate = build_senate_roster([_sponsor(100, 1, "Stanford", agency="Senate")])
    links = build_senate_identity_links(
        [{"person_id": "800", "filer_name": "Derek Stanford", "legislative_district": ""}],
        senate_roster=senate,
    )
    assert links.identifiers == []
    assert links.summary["incomplete"] == 1
    assert links.summary["matched"] == 0


def test_senate_link_unresolved_when_ambiguous():
    """Two same-surname senators in an LD → the winner can't be resolved to one, so it's left
    ``unresolved`` (never guessed) — the #75 robustness signal."""
    senate = build_senate_roster(
        [
            _sponsor(100, 1, "Stanford", agency="Senate"),
            _sponsor(101, 1, "Stanford", agency="Senate"),
        ]
    )
    links = build_senate_identity_links(
        [_winner("800", 1, 0, "Derek Stanford")], senate_roster=senate
    )
    assert links.identifiers == []
    assert links.summary["unresolved"] == 1
