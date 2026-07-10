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


def test_incomplete_row_counted_not_emitted():
    proj = build_house_position_observations(
        [{"person_id": "", "legislative_district": "5", "position": "1", "filer_name": "X"}],
        house_roster={},
        senate_roster={},
        biennium=BIENNIUM,
    )
    assert proj.observations == []
    assert proj.summary["incomplete"] == 1
