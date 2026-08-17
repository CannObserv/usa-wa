"""Roster proposals → writable operator events (#226 write half).

The resolver answers the two questions a proposal cannot: *which member* the roster row names,
and — for a House seat — *which Position* they held. Both are lookups against corpora we
already hold, so the tests here are about the refusals: every case where a guess would assert
a seat or a person the evidence does not support.
"""

from __future__ import annotations

from datetime import date

import pytest

from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.resolve import (
    UNRESOLVED_AMBIGUOUS_MEMBER,
    UNRESOLVED_AMBIGUOUS_POSITION,
    UNRESOLVED_GIVEN_NAME_MISMATCH,
    UNRESOLVED_NO_MEMBER,
    UNRESOLVED_NO_POSITION,
    PositionTenure,
    ResolvedEvent,
    Seating,
    SuccessionResolver,
    Unresolved,
    resolution_summary,
)
from usa_wa_adapter_legislature.roster_pdf.succession import propose_events


def _proposal(annotation: str, **kw):
    base = dict(
        district=2,
        chamber="house",
        year=2013,
        order=1,
        name="Graham Hunt",
        party_token="R",
        page_number=1,
    )
    base.update(kw)
    report = propose_events([RosterRecord(annotation=annotation, **base)])
    both = report.proposals + report.unseated
    assert len(both) == 1, both
    return both[0]


HUNT = Seating(member_id="21234", chamber="house", district=2, year=2014, surname="Hunt")
HUNT_POSITION = PositionTenure(
    member_id="21234", district=2, position="2", first_year=2013, last_year=2016
)

#: The real LD2 Position 1 lineage, verbatim from the span corpus. Graham Hunt was appointed
#: 2014-01-17 — in the *2013-14* biennium — yet his only House Position span is
#: ``18517:chamber-house:ld-2-position-1:2015-16``, opening 2015-01-01. The biennium he was
#: appointed into is exactly the one the quantized corpus missed, which is *why* the event
#: matters; a rule requiring the event to fall inside the span can never resolve it.
HUNT_REAL = Seating(member_id="18517", chamber="house", district=2, year=2014, surname="Hunt")
HUNT_REAL_POSITION = PositionTenure(
    member_id="18517", district=2, position="1", first_year=2015, last_year=2016
)


class TestMemberResolution:
    def test_resolves_a_senate_departure_to_its_member_id(self) -> None:
        proposal = _proposal("Deceased June 15, 1979", chamber="senate", year=1977, name="Al Henry")
        resolver = SuccessionResolver(
            seatings=[
                Seating(member_id="99", chamber="senate", district=2, year=1979, surname="Henry")
            ],
            positions=[],
        )
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, ResolvedEvent)
        assert resolved.member_id == "99"
        assert resolved.kind == "departed"
        assert (resolved.seat_kind, resolved.seat_discriminator) == (None, None)

    def test_an_unknown_member_is_unresolved_not_guessed(self) -> None:
        """Pre-1991 is outside the sponsor roster entirely. No Person exists to attach the
        event to, and inventing one would mint a duplicate identity in Power Map."""
        proposal = _proposal("Deceased June 15, 1929", chamber="senate", year=1927)
        resolved = SuccessionResolver(seatings=[], positions=[]).resolve(proposal)
        assert isinstance(resolved, Unresolved)
        assert resolved.reason == UNRESOLVED_NO_MEMBER

    def test_two_same_surname_holders_of_one_seat_are_ambiguous(self) -> None:
        """A district that seated two Chandlers cannot be disambiguated by surname alone;
        picking either would move the wrong person's span."""
        proposal = _proposal("Deceased June 15, 1994", chamber="senate", year=1993, name="Chandler")
        resolver = SuccessionResolver(
            seatings=[
                Seating(
                    member_id="75", chamber="senate", district=2, year=1994, surname="Chandler"
                ),
                Seating(
                    member_id="76", chamber="senate", district=2, year=1994, surname="Chandler"
                ),
            ],
            positions=[],
        )
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, Unresolved)
        assert resolved.reason == UNRESOLVED_AMBIGUOUS_MEMBER

    def test_matches_on_the_effective_year_when_it_differs_from_the_session_year(self) -> None:
        """``Deceased June 15, 1979`` sits on a 1977 roster row: the death is in the *next*
        biennium. Keying only on the row's year would miss the member entirely."""
        proposal = _proposal("Deceased June 15, 1979", chamber="senate", year=1977, name="Al Henry")
        resolver = SuccessionResolver(
            seatings=[
                Seating(member_id="99", chamber="senate", district=2, year=1979, surname="Henry")
            ],
            positions=[],
        )
        assert isinstance(resolver.resolve(proposal), ResolvedEvent)

    def test_a_chamber_mismatch_does_not_match(self) -> None:
        """The same person can sit in both chambers across a career; the roster row states
        which one this fact belongs to."""
        proposal = _proposal("Deceased June 15, 1979", chamber="senate", year=1979, name="Al Henry")
        resolver = SuccessionResolver(
            seatings=[
                Seating(member_id="99", chamber="house", district=2, year=1979, surname="Henry")
            ],
            positions=[],
        )
        assert isinstance(resolver.resolve(proposal), Unresolved)


class TestSenateSeats:
    def test_a_senate_seat_needs_no_lookup(self) -> None:
        """The roster states the LD outright, which *is* the Senate discriminator."""
        proposal = _proposal(
            "Appointed August 13, 1979 to serve unexpired term",
            chamber="senate",
            year=1979,
            district=3,
            name="Al Henry",
        )
        resolver = SuccessionResolver(
            seatings=[
                Seating(member_id="99", chamber="senate", district=3, year=1979, surname="Henry")
            ],
            positions=[],
        )
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, ResolvedEvent)
        assert (resolved.seat_kind, resolved.seat_discriminator) == ("chamber-senate", "3")


class TestHousePositions:
    def test_positions_a_house_seating_from_the_span_corpus(self) -> None:
        """The roster dates Hunt's appointment but cannot say Position 2; the existing
        House Position span can, and that is the whole point of the write half."""
        proposal = _proposal("Appointed January 17, 2014 to serve unexpired term")
        resolver = SuccessionResolver(seatings=[HUNT], positions=[HUNT_POSITION])
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, ResolvedEvent)
        assert resolved.seat_kind == "chamber-house"
        assert resolved.seat_discriminator == "ld-2-position-2"

    def test_a_pre_position_house_seating_is_unresolved(self) -> None:
        """House Position coverage floors at 2003 (#118 back-chain). Before that the roster
        knows the date and nothing knows the seat — that is #229's job, not a guess."""
        proposal = _proposal(
            "Appointed December 18, 1987 to serve unexpired term",
            year=1987,
            name="Randy Dorn",
        )
        resolver = SuccessionResolver(
            seatings=[
                Seating(member_id="55", chamber="house", district=2, year=1987, surname="Dorn")
            ],
            positions=[],
        )
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, Unresolved)
        assert resolved.reason == UNRESOLVED_NO_POSITION

    def test_a_position_span_for_another_district_does_not_position_the_seat(self) -> None:
        """A member who moved LDs has spans in both; only the one the roster names applies."""
        proposal = _proposal("Appointed January 17, 2014 to serve unexpired term")
        resolver = SuccessionResolver(
            seatings=[HUNT],
            positions=[
                PositionTenure(
                    member_id="21234", district=7, position="1", first_year=2013, last_year=2016
                )
            ],
        )
        assert isinstance(resolver.resolve(proposal), Unresolved)

    def test_a_mid_biennium_appointment_reaches_the_span_it_opens(self) -> None:
        """The real LD2 oracle. Hunt's appointment (2014-01-17) sits one year *before* his
        only Position span (2015-16), because the quantized corpus never recorded the partial
        biennium he was appointed into — that gap is the defect this backfill exists to fix.
        Requiring the event to fall inside the span would refuse exactly the events that
        matter most."""
        proposal = _proposal("Appointed January 17, 2014 to serve unexpired term")
        resolver = SuccessionResolver(seatings=[HUNT_REAL], positions=[HUNT_REAL_POSITION])
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, ResolvedEvent)
        assert resolved.seat_discriminator == "ld-2-position-1"

    def test_a_span_two_bienniums_later_does_not_position_the_seat(self) -> None:
        """The reach back is one year — the span whose opening biennium the seating belongs
        to. A span two bienniums out is a different tenure, and a member who returns to an LD
        may well return to the other Position."""
        proposal = _proposal("Appointed January 17, 2014 to serve unexpired term")
        resolver = SuccessionResolver(
            seatings=[HUNT],
            positions=[
                PositionTenure(
                    member_id="21234", district=2, position="1", first_year=2017, last_year=2020
                )
            ],
        )
        assert isinstance(resolver.resolve(proposal), Unresolved)

    def test_two_positions_covering_one_year_are_ambiguous(self) -> None:
        """Holding both Positions of one LD at once is impossible, so the corpus is telling
        us something is wrong. Writing either would assert a seat on that bad footing."""
        proposal = _proposal("Appointed January 17, 2014 to serve unexpired term")
        resolver = SuccessionResolver(
            seatings=[HUNT],
            positions=[
                HUNT_POSITION,
                PositionTenure(
                    member_id="21234", district=2, position="1", first_year=2013, last_year=2016
                ),
            ],
        )
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, Unresolved)
        assert resolved.reason == UNRESOLVED_AMBIGUOUS_POSITION

    def test_a_house_departure_needs_no_position(self) -> None:
        """``departed`` is person-scoped, so a pre-2003 House death is writable today."""
        proposal = _proposal("Deceased June 15, 1979", year=1979, name="Al Henry")
        resolver = SuccessionResolver(
            seatings=[
                Seating(member_id="99", chamber="house", district=2, year=1979, surname="Henry")
            ],
            positions=[],
        )
        assert isinstance(resolver.resolve(proposal), ResolvedEvent)


class TestBatchResolution:
    def test_every_proposal_lands_in_exactly_one_bucket(self) -> None:
        proposals = [
            _proposal("Deceased June 15, 1979", chamber="senate", year=1979, name="Al Henry"),
            _proposal("Appointed January 17, 2014 to serve unexpired term"),
            _proposal("Deceased June 15, 1929", chamber="senate", year=1927),
        ]
        resolver = SuccessionResolver(
            seatings=[
                HUNT,
                Seating(member_id="99", chamber="senate", district=2, year=1979, surname="Henry"),
            ],
            positions=[HUNT_POSITION],
        )
        outcome = resolver.resolve_all(proposals)
        assert len(outcome.resolved) + len(outcome.unresolved) == len(proposals)
        assert len(outcome.resolved) == 2

    def test_summary_counts_by_kind_and_by_refusal(self) -> None:
        resolver = SuccessionResolver(seatings=[HUNT], positions=[HUNT_POSITION])
        outcome = resolver.resolve_all(
            [
                _proposal("Appointed January 17, 2014 to serve unexpired term"),
                _proposal("Deceased June 15, 1929", chamber="senate", year=1927),
            ]
        )
        assert resolution_summary(outcome) == {
            "seated:appointed": 1,
            f"unresolved:{UNRESOLVED_NO_MEMBER}": 1,
        }


class TestEventIdentity:
    @pytest.mark.parametrize("kind", ["departed", "seated"])
    def test_resolution_preserves_the_boundary_verbatim(self, kind: str) -> None:
        """The resolver adds identity; it must never move a date or reclassify a boundary."""
        annotation = (
            "Deceased June 15, 1979"
            if kind == "departed"
            else "Appointed January 17, 2014 to serve unexpired term"
        )
        year = 1979 if kind == "departed" else 2014
        proposal = _proposal(annotation, chamber="senate", year=year, name="Al Henry")
        resolver = SuccessionResolver(
            seatings=[
                Seating(member_id="99", chamber="senate", district=2, year=year, surname="Henry")
            ],
            positions=[],
        )
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, ResolvedEvent)
        assert resolved.kind == proposal.kind
        assert resolved.reason == proposal.reason
        assert resolved.effective_date == proposal.effective_date
        assert resolved.effective_date == date(year, *((6, 15) if kind == "departed" else (1, 17)))


class TestGivenNameGuard:
    """#240: the ambiguity check cannot fire when the roster row's true subject is **absent**
    from the sponsor index — the single surviving surname match is then a *false* match, not an
    ambiguous one.

    The live case: LD16 House 2009 carries `William A. Grant — Deceased January 4, 2009` and his
    successor `Laura Grant-Herriot — Appointed Feb. 20, 2009`. WSL records *her* LastName as
    `Grant`, and *he* is absent from the 2009-10 sponsor roster because he died before the
    snapshot. His death was attributed to her, closing every one of her spans 18 days before she
    was appointed — and silently disabling a correct operator attestation, since the overlay only
    applies a seat event whose date falls inside the span window.
    """

    GRANT_HERRIOT = Seating(
        member_id="14874",
        chamber="house",
        district=16,
        year=2009,
        surname="Grant",
        given_name="Laura",
    )

    def test_a_dead_predecessor_is_not_resolved_to_his_successor(self) -> None:
        proposal = _proposal(
            "Deceased January 4, 2009",
            chamber="house",
            district=16,
            year=2009,
            name="William A. Grant",
        )
        resolver = SuccessionResolver(seatings=[self.GRANT_HERRIOT], positions=[])
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, Unresolved)
        assert resolved.reason == UNRESOLVED_GIVEN_NAME_MISMATCH

    def test_the_successor_still_resolves_on_her_own_row(self) -> None:
        """The guard must not cost the match that is actually correct."""
        proposal = _proposal(
            "Appointed Feb. 20, 2009",
            chamber="house",
            district=16,
            year=2009,
            name="Laura Grant-Herriot",
        )
        resolver = SuccessionResolver(
            seatings=[
                Seating(
                    member_id="14874",
                    chamber="house",
                    district=16,
                    year=2009,
                    surname="Grant-Herriot",
                    given_name="Laura",
                )
            ],
            positions=[
                PositionTenure(
                    member_id="14874", district=16, position="2", first_year=2009, last_year=2010
                )
            ],
        )
        assert isinstance(resolver.resolve(proposal), ResolvedEvent)

    @pytest.mark.parametrize(
        ("roster_name", "given_name"),
        [
            # Every benign variant the live corpus contains. A nickname, a formal name, an
            # initial and a middle-name-first row all share the given-name initial; two
            # different people do not.
            ("Mike Padden", "Michael"),
            ("Art Wang", "Arthur"),
            ("Jim Springer", "James"),
            ("Zachary Hall", "Zach"),
            ("Edward B. Murray", "Ed"),
            ("J. Bruce Holland", "Jeffrey"),
            ("Louise Miller", "C Louise"),
            ("Mike Riley", "Moyne"),
            ("Sidney R. Snyder", "Sid"),
            ("Alvin C. Williams", "Al"),
        ],
    )
    def test_benign_name_variants_still_match(self, roster_name: str, given_name: str) -> None:
        """19 of the 20 flagged rows were variants like these; refusing them would throw away
        real corrections to buy nothing."""
        proposal = _proposal(
            "Deceased June 15, 1979", chamber="senate", year=1979, name=roster_name
        )
        resolver = SuccessionResolver(
            seatings=[
                Seating(
                    member_id="99",
                    chamber="senate",
                    district=2,
                    year=1979,
                    surname=roster_name.split()[-1],
                    given_name=given_name,
                )
            ],
            positions=[],
        )
        assert isinstance(resolver.resolve(proposal), ResolvedEvent), roster_name

    def test_a_missing_given_name_does_not_refuse(self) -> None:
        """Absence of the signal is not evidence against the match — the sponsor roster does
        not always carry a first name, and refusing on that would be a silent coverage loss."""
        proposal = _proposal("Deceased June 15, 1979", chamber="senate", year=1979, name="Al Henry")
        resolver = SuccessionResolver(
            seatings=[
                Seating(
                    member_id="99",
                    chamber="senate",
                    district=2,
                    year=1979,
                    surname="Henry",
                    given_name="",
                )
            ],
            positions=[],
        )
        assert isinstance(resolver.resolve(proposal), ResolvedEvent)

    def test_a_compatible_match_wins_over_an_incompatible_one(self) -> None:
        """When the index holds both the real subject and a same-surname lookalike, the guard
        selects rather than refuses."""
        proposal = _proposal(
            "Deceased January 4, 2009",
            chamber="house",
            district=16,
            year=2009,
            name="William A. Grant",
        )
        resolver = SuccessionResolver(
            seatings=[
                self.GRANT_HERRIOT,
                Seating(
                    member_id="8000",
                    chamber="house",
                    district=16,
                    year=2009,
                    surname="Grant",
                    given_name="William",
                ),
            ],
            positions=[],
        )
        resolved = resolver.resolve(proposal)
        assert isinstance(resolved, ResolvedEvent)
        assert resolved.member_id == "8000"
