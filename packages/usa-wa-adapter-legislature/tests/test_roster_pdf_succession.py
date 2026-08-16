"""Roster annotation → operator-event proposals (#226, epic #219 Phase 2).

Every string here is verbatim from the 2025-06-05 edition. The parser exists to survive ~90
years of clerks' prose, so invented examples would test the wrong thing.
"""

from __future__ import annotations

from datetime import date

import pytest

from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.succession import (
    DEFER_HOUSE_SEAT_UNRESOLVED,
    DEFER_NO_DAY_PRECISION,
    parse_annotation,
    propose_events,
)


def _record(annotation: str, **kw) -> RosterRecord:
    base = dict(
        district=2,
        chamber="house",
        year=2013,
        order=1,
        name="Gary C. Alexander",
        party_token="R",
        page_number=1,
    )
    base.update(kw)
    return RosterRecord(annotation=annotation, **base)


class TestDateParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Resigned Dec. 31, 2013", date(2013, 12, 31)),
            ("Resigned January 24, 1957", date(1957, 1, 24)),
            ("Deceased March 8, 1917", date(1917, 3, 8)),
            ("Elected Nov 5, 1968 to serve unexpired term", date(1968, 11, 5)),
            # The source contains typographic noise: a period where a comma belongs, and a
            # stray period after the day. Both are real, both must still parse.
            ("Sworn in December 6., 2024 to serve unexpired term", date(2024, 12, 6)),
            ("Sworn in February 4. 2013", date(2013, 2, 4)),
        ],
    )
    def test_parses_day_precision(self, text: str, expected: date) -> None:
        clauses = parse_annotation(text)
        assert any(c.parsed.value == expected and c.parsed.precision == "day" for c in clauses)

    @pytest.mark.parametrize(
        ("text", "precision"),
        [
            ("Deceased July 1977", "month"),
            ("Resigned February 1944", "month"),
            ("Elected in 1922 to serve unexpired term", "year"),
            ("Resigned; Appointed to the Senate", "none"),
            ("Redistricted out of district", "none"),
        ],
    )
    def test_records_coarser_precision_rather_than_inventing_a_day(
        self, text: str, precision: str
    ) -> None:
        clauses = parse_annotation(text)
        assert clauses
        assert clauses[0].parsed.precision == precision

    def test_a_session_reference_is_not_a_date(self) -> None:
        """``to serve 1951 2nd Ex. S.`` names a legislative session, not an appointment date.
        Reading it as a year would date an event to the wrong thing entirely."""
        clauses = parse_annotation("Appointed to serve 1951 2nd Ex. S.")
        assert all(c.parsed.precision == "none" for c in clauses)

    def test_a_holdover_district_reference_is_not_a_date(self) -> None:
        clauses = parse_annotation("Holdover from District 21, 1901 Session")
        assert all(c.parsed.precision == "none" for c in clauses)


class TestClauseSplitting:
    def test_splits_on_semicolons(self) -> None:
        clauses = parse_annotation(
            "Elected Nov. 6, 2007; Sworn in November 29, 2007 to serve unexpired term"
        )
        assert [c.verb for c in clauses] == ["elected", "sworn_in"]

    def test_keeps_each_clause_date(self) -> None:
        clauses = parse_annotation(
            "Appointed April 18, 1966; Elected Nov. 8, 1966 to serve unexpired term"
        )
        assert clauses[0].parsed.value == date(1966, 4, 18)
        assert clauses[1].parsed.value == date(1966, 11, 8)


class TestProposals:
    def test_a_death_is_a_person_scoped_departure(self) -> None:
        """``departed`` closes every open span, so it needs no seat — which is why deaths are
        emittable for both chambers today while seat-scoped kinds are not."""
        report = propose_events([_record("Deceased June 15, 1979", chamber="senate", year=1977)])
        (proposal,) = report.proposals
        assert proposal.kind == "departed"
        assert proposal.reason == "died"
        assert proposal.effective_date == date(1979, 6, 15)
        assert proposal.seat_kind is None
        assert proposal.seat_discriminator is None

    def test_a_full_resignation_is_a_departure(self) -> None:
        report = propose_events([_record("Resigned Jan. 13, 1993", chamber="senate", year=1993)])
        (proposal,) = report.proposals
        assert (proposal.kind, proposal.reason) == ("departed", "resigned")

    def test_a_chamber_move_is_a_seat_vacancy_not_a_departure(self) -> None:
        """``Resigned; Appointed to the Senate`` is a move: the member keeps serving, so
        closing every span would wrongly end their party tenure too."""
        report = propose_events(
            [
                _record(
                    "Resigned January 24, 1957; Appointed to the Senate",
                    chamber="senate",
                    year=1957,
                )
            ]
        )
        (proposal,) = report.proposals
        assert (proposal.kind, proposal.reason) == ("vacated", "moved")

    def test_an_appointment_seats_the_member(self) -> None:
        report = propose_events(
            [
                _record(
                    "Appointed August 13, 1979 to serve unexpired term",
                    chamber="senate",
                    year=1977,
                    district=3,
                )
            ]
        )
        (proposal,) = report.proposals
        assert (proposal.kind, proposal.reason) == ("seated", "appointed")
        assert proposal.seat_kind == "chamber-senate"
        assert proposal.seat_discriminator == "3"

    def test_a_swearing_in_wins_over_the_election_date(self) -> None:
        """Service starts when sworn, not when elected — using the ballot date would open the
        span weeks early."""
        report = propose_events(
            [
                _record(
                    "Elected Nov. 6, 2007; Sworn in November 29, 2007 to serve unexpired term",
                    chamber="senate",
                    year=2007,
                )
            ]
        )
        (proposal,) = report.proposals
        assert proposal.reason == "sworn_in"
        assert proposal.effective_date == date(2007, 11, 29)


class TestDeferrals:
    def test_a_house_seat_event_defers_on_the_missing_position(self) -> None:
        """A House seat is ``ld-{n}-position-{p}`` and the roster carries no Position, so a
        seat-scoped House event cannot name its seat until #229 supplies the discriminator.
        Deferring is the only honest option — guessing a position would assert a false seat."""
        report = propose_events(
            [_record("Appointed January 17, 2014 to serve unexpired term", chamber="house")]
        )
        assert report.proposals == ()
        assert report.deferred[0].reason == DEFER_HOUSE_SEAT_UNRESOLVED

    def test_a_house_death_still_proposes(self) -> None:
        """Person-scoped events need no seat, so the House is not blocked for those."""
        report = propose_events([_record("Deceased June 15, 1979", chamber="house")])
        assert report.proposals[0].kind == "departed"

    def test_a_month_precision_date_is_deferred_not_rounded(self) -> None:
        """Rounding ``February 1944`` to the 1st invents a boundary the source never asserted."""
        report = propose_events([_record("Resigned February 1944", chamber="senate")])
        assert report.proposals == ()
        assert report.deferred[0].reason == DEFER_NO_DAY_PRECISION

    def test_every_annotation_is_accounted_for(self) -> None:
        """Report-don't-drop: an annotation yields a proposal or a deferral, never silence."""
        records = [
            _record("Deceased June 15, 1979", chamber="senate"),
            _record("Redistricted out of district", chamber="senate"),
            _record("Resigned February 1944", chamber="senate"),
            _record("Speaker", chamber="senate"),
        ]
        report = propose_events(records)
        assert len(report.proposals) + len(report.deferred) == len(records)
