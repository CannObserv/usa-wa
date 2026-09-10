"""Roster annotation → operator-event proposals (#226, epic #219 Phase 2).

Every string here is verbatim from the 2025-06-05 edition. The parser exists to survive ~90
years of clerks' prose, so invented examples would test the wrong thing.
"""

from __future__ import annotations

from datetime import date

import pytest

from usa_wa_adapter_legislature.roster_pdf import succession
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.succession import (
    DEFER_NO_DAY_PRECISION,
    parse_annotation,
    proposals_for_seat,
    propose_events,
    summarize,
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
    def test_a_house_seat_event_is_unseated_not_deferred(self) -> None:
        """A House seat is ``ld-{n}-position-{p}`` and the roster carries no Position, so a
        seat-scoped House event cannot name its seat *from the roster alone*. That is a
        missing discriminator, not a missing boundary — the date is perfectly good — so it
        lands in ``unseated`` with the boundary intact, where the write half can supply the
        Position from the existing span corpus. Folding it into ``deferred`` discarded the
        kind, reason and date, which is what made these 224 records unrecoverable."""
        report = propose_events(
            [_record("Appointed January 17, 2014 to serve unexpired term", chamber="house")]
        )
        assert report.proposals == ()
        assert report.deferred == ()
        (unseated,) = report.unseated
        assert unseated.kind == "seated"
        assert unseated.reason == "appointed"
        assert unseated.effective_date == date(2014, 1, 17)
        assert unseated.seat_kind == "chamber-house"
        assert unseated.seat_discriminator is None

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
        """Report-don't-drop: an annotation yields a proposal, an unseated proposal or a
        deferral, never silence."""
        records = [
            _record("Deceased June 15, 1979", chamber="senate"),
            _record("Redistricted out of district", chamber="senate"),
            _record("Resigned February 1944", chamber="senate"),
            _record("Speaker", chamber="senate"),
            _record("Appointed January 17, 2014", chamber="house"),
        ]
        report = propose_events(records)
        assert len(report.proposals) + len(report.unseated) + len(report.deferred) == len(records)


class TestMultipleBoundaries:
    """An annotation routinely states both ends of a tenure; emitting only one loses the other.

    Every string here is verbatim from the 2025-06-05 edition.
    """

    def test_a_seating_and_a_departure_both_emit(self) -> None:
        """Jesernig, LD8 Senate: sworn in 1990, resigned 1993. Taking only the seating left the
        span open for three years; taking only the departure lost his real start."""
        report = propose_events(
            [
                _record(
                    "Elected Nov. 6, 1990; Sworn in Nov. 30, 1990 to serve unexpired term; "
                    "Resigned Nov. 9, 1993; Appointed Director, Dptmnt. of Ag.",
                    chamber="senate",
                    district=8,
                    year=1991,
                )
            ]
        )
        assert {(p.kind, p.reason, p.effective_date) for p in report.proposals} == {
            ("seated", "sworn_in", date(1990, 11, 30)),
            ("departed", "resigned", date(1993, 11, 9)),
        }

    def test_a_dateless_external_appointment_does_not_swallow_the_departure(self) -> None:
        """CR finding 10: ``Appointed <external office>`` carries no date, and capturing the
        seating branch discarded the dated resignation entirely — 30 departures across the
        corpus."""
        report = propose_events(
            [
                _record(
                    "Resigned August 24, 1949; Appointed Employment Security Commissioner",
                    chamber="senate",
                    district=8,
                    year=1949,
                )
            ]
        )
        assert [(p.kind, p.reason, p.effective_date) for p in report.proposals] == [
            ("departed", "resigned", date(1949, 8, 24))
        ]

    def test_a_temporary_appointment_emits_both_ends(self) -> None:
        """Braun, LD20 Senate — the five-day military substitution. A ``seated`` with no end
        asserts he held the seat indefinitely, which is worse than the biennium floor it
        replaces and is the ghost-open span #107/#119 exist to prevent."""
        report = propose_events(
            [
                _record(
                    "Appointed to temporarily serve from July 18, 2017 until July 23, 2017",
                    chamber="senate",
                    district=20,
                    year=2017,
                )
            ]
        )
        assert {(p.kind, p.effective_date) for p in report.proposals} == {
            ("seated", date(2017, 7, 18)),
            ("departed", date(2017, 7, 23)),
        }

    def test_a_swearing_in_still_collapses_with_its_appointment(self) -> None:
        """Two dates for the SAME boundary must stay one event — emitting both would put two
        starts on one seat. Sharon Brown: appointed Jan 28, sworn Feb 4."""
        report = propose_events(
            [
                _record(
                    "Appointed January 28, 2013; Sworn in February 4. 2013; "
                    "Elected Nov. 5, 2013 to serve unexpired term",
                    chamber="senate",
                    district=8,
                    year=2011,
                )
            ]
        )
        seatings = [p for p in report.proposals if p.kind == "seated"]
        assert len(seatings) == 1
        assert seatings[0].effective_date == date(2013, 2, 4)


class TestProvenance:
    def test_proposals_and_deferrals_carry_the_source_page(self) -> None:
        """CR finding 14: an operator adjudicating 631 deferrals needs a way back into a
        233-page document."""
        report = propose_events(
            [
                _record("Deceased June 15, 1979", chamber="senate", page_number=42),
                _record("Speaker", chamber="senate", page_number=43),
            ]
        )
        assert report.proposals[0].page_number == 42
        assert report.deferred[0].page_number == 43


class TestReportHelpers:
    """CR finding 13 — both are how a human judges the backfill, so both are pinned."""

    def test_summarize_counts_by_kind_and_reason(self) -> None:
        report = propose_events(
            [
                _record("Deceased June 15, 1979", chamber="senate"),
                _record("Deceased July 6, 1950", chamber="senate"),
                _record("Speaker", chamber="senate"),
            ]
        )
        assert summarize(report) == {
            "departed:died": 2,
            "deferred:no_succession_verb": 1,
        }

    def test_summarize_counts_unseated_separately(self) -> None:
        """An unseated House boundary is not a refusal — counting it as one understates what
        the backfill can write once the Position is resolved."""
        report = propose_events(
            [_record("Appointed January 17, 2014", chamber="house"), _record("Speaker")]
        )
        assert summarize(report) == {
            "unseated:seated:appointed": 1,
            "deferred:no_succession_verb": 1,
        }

    def test_proposals_for_seat_filters_and_orders_oldest_first(self) -> None:
        report = propose_events(
            [
                _record("Resigned Dec. 31, 2013", chamber="senate", district=2),
                _record("Deceased June 15, 1979", chamber="senate", district=2),
                _record("Resigned Jan. 13, 1993", chamber="senate", district=9),
            ]
        )
        seat = proposals_for_seat(report, district=2, chamber="senate")
        assert [p.effective_date for p in seat] == [date(1979, 6, 15), date(2013, 12, 31)]


def test_millennium_off_year_is_not_a_day_precision_date() -> None:
    """LD9 1921 prints 'special election January 7, 2921' — a source typo. A year outside
    the plausible window must not become a day-precision date that silently shapes #226
    boundaries and #228 coverage; it degrades to coarser precision (CR #75)."""
    clauses = parse_annotation(
        "Elected in special election January 7, 2921 to serve unexpired term"
    )
    assert all(c.parsed.precision != "day" for c in clauses)


class TestStatedWindow:
    """#360: a month-only date is not *no* date — it is a date we know to a month.

    `_parse_date` parsed the month and year and then dropped both, so
    "Appointed Oct. 1971" reached the span builder as nothing and the successor
    fell back to the biennium floor — overlapping a predecessor whose own exit
    the roster dated precisely. The window is the smallest honest thing to keep:
    it lets a consumer say "certainly after" without inventing a day.
    """

    def test_a_day_precise_date_is_a_single_day_window(self) -> None:
        parsed = succession._parse_date("Resigned January 13, 1997")
        assert parsed.precision == "day"
        assert parsed.value == date(1997, 1, 13)
        assert parsed.window == (date(1997, 1, 13), date(1997, 1, 13))

    def test_a_month_only_date_keeps_its_month_as_a_window(self) -> None:
        parsed = succession._parse_date("Appointed Oct. 1971")
        assert parsed.precision == "month"
        # `value` stays None: only a day-precise date is ever an effective date,
        # and widening that would silently start seating people on the 1st
        assert parsed.value is None
        assert parsed.window == (date(1971, 10, 1), date(1971, 10, 31))

    def test_february_window_respects_leap_years(self) -> None:
        assert succession._parse_date("Appointed Feb. 1972").window == (
            date(1972, 2, 1),
            date(1972, 2, 29),
        )
        assert succession._parse_date("Appointed Feb. 1971").window == (
            date(1971, 2, 1),
            date(1971, 2, 28),
        )

    def test_a_year_only_date_keeps_the_year_as_a_window(self) -> None:
        parsed = succession._parse_date("Resigned 1963; Appointed District Dir.")
        assert parsed.precision == "year"
        assert parsed.window == (date(1963, 1, 1), date(1963, 12, 31))

    def test_no_date_has_no_window(self) -> None:
        parsed = succession._parse_date("Appointed to State Liquor Control Board")
        assert parsed.precision == "none"
        assert parsed.window is None

    def test_an_impossible_day_falls_back_to_its_month_window(self) -> None:
        """Feb 31 is not a date, but "February 1931" still bounds it — the
        fallback should not throw the month away with the day."""
        parsed = succession._parse_date("Resigned February 31, 1931")
        assert parsed.value is None
        assert parsed.window == (date(1931, 2, 1), date(1931, 2, 28))

    def test_an_out_of_range_year_states_no_date_at_all(self) -> None:
        """CR 125/126: the `_YEAR` regex admits 1800-1888 and 2050-2099, which
        the day and month branches reject. Washington was not a state before
        1889, so a stray year in a clerk's prose must not become a confident
        bound just because it reached the coarsest branch."""
        for text in ("Resigned 1850", "Resigned 1888", "Elected 2099"):
            parsed = succession._parse_date(text)
            assert parsed.precision == "none", text
            assert parsed.window is None, text

        for text in ("Resigned 1889", "Elected 2049"):
            assert succession._parse_date(text).window is not None, text

    def test_an_out_of_range_month_states_no_date_either(self) -> None:
        """The same rule one branch up: `Oct. 1066` used to return month
        precision with no window, breaking the invariant below."""
        parsed = succession._parse_date("Appointed Oct. 1066")
        assert parsed.precision == "none"
        assert parsed.window is None

    def test_a_window_exists_exactly_when_a_date_was_stated(self) -> None:
        """The invariant the docstring promises, asserted rather than described:
        a consumer may branch on either and get the same answer."""
        cases = [
            "Resigned January 13, 1997",
            "Appointed Oct. 1971",
            "Resigned 1963",
            "Appointed to State Liquor Control Board",
            "Appointed Oct. 1066",
            "Resigned February 31, 1931",
        ]
        for text in cases:
            parsed = succession._parse_date(text)
            assert (parsed.window is not None) == (parsed.precision != "none"), text


class TestChamberMoveAcrossRecords:
    """usa-wa#363. A resignation vacates ONE seat when the member is moving; it
    departs the person only when they are leaving the legislature. The roster
    states a move across two rows — the resignation on the chamber being left,
    the seating on the chamber being joined — and `_MOVE` only ever read the
    single row in front of it.
    """

    STANFORD = [
        _record(
            "Resigned July 1, 2019", chamber="house", district=1, year=2019, name="Derek Stanford"
        ),
        _record(
            "Appointed July 1, 2019 to serve unexpired term",
            chamber="senate",
            district=1,
            year=2017,
            name="Derek Stanford",
        ),
    ]
    CHAPMAN = [
        _record(
            "Resigned December 5, 2024",
            chamber="house",
            district=24,
            year=2023,
            name="Mike Chapman",
        ),
        _record(
            "Elected Nov. 5, 2024; Sworn in December 6., 2024 to serve unexpired term",
            chamber="senate",
            district=24,
            year=2025,
            name="Mike Chapman",
        ),
    ]

    def _ends(self, records):
        report = propose_events(records)
        return {
            (p.kind, p.reason)
            for p in report.proposals + report.unseated
            if p.kind in ("departed", "vacated")
        }

    def test_a_same_day_move_vacates_rather_than_departs(self):
        assert self._ends(self.STANFORD) == {("vacated", "moved")}

    def test_a_next_day_move_vacates_too(self):
        """Chapman resigned the House on the 5th and was sworn into the Senate on
        the 6th. One day is still one move."""
        assert self._ends(self.CHAPMAN) == {("vacated", "moved")}

    def test_the_vacated_names_the_seat_being_left(self):
        (vacated,) = [p for p in propose_events(self.STANFORD).unseated if p.kind == "vacated"]
        assert vacated.seat_kind == "chamber-house"
        assert vacated.chamber == "house"

    def test_a_seating_in_the_same_chamber_is_not_a_move(self):
        """The roster mangles a successor's appointment into the incumbent's own
        cell — Christine Rolfes resigned for the Kitsap County Commission and the
        cell also carries her successor's seating. Same chamber, same row: she
        left the legislature, and `departed` is correct.
        """
        rolfes = [
            _record(
                "Resigned August 15, 2023, Appointed to Kitsap County Commission) "
                "(Appointed August 23, 2023; D Sworn in Aug. 28, 2023 to serve unexpired term)",
                chamber="senate",
                district=23,
                year=2021,
                name="Christine Rolfes",
            )
        ]
        assert self._ends(rolfes) == {("departed", "resigned")}

    def test_an_unrelated_member_seating_does_not_move_anyone(self):
        records = [
            _record(
                "Resigned July 1, 2019",
                chamber="house",
                district=1,
                year=2019,
                name="Derek Stanford",
            ),
            _record(
                "Appointed July 1, 2019 to serve unexpired term",
                chamber="senate",
                district=9,
                year=2017,
                name="Somebody Else",
            ),
        ]
        assert self._ends(records) == {("departed", "resigned")}

    def test_a_seating_long_after_the_resignation_is_not_a_move(self):
        """A member who leaves and is appointed to the other chamber years later
        did depart in between; only a handoff within the window is one move."""
        records = [
            _record(
                "Resigned July 1, 2019",
                chamber="house",
                district=1,
                year=2019,
                name="Derek Stanford",
            ),
            _record(
                "Appointed March 4, 2021 to serve unexpired term",
                chamber="senate",
                district=1,
                year=2021,
                name="Derek Stanford",
            ),
        ]
        assert self._ends(records) == {("departed", "resigned")}

    def test_a_seating_well_before_the_resignation_is_not_a_move(self):
        records = [
            _record(
                "Resigned July 1, 2019",
                chamber="house",
                district=1,
                year=2019,
                name="Derek Stanford",
            ),
            _record(
                "Appointed January 9, 2019 to serve unexpired term",
                chamber="senate",
                district=1,
                year=2019,
                name="Derek Stanford",
            ),
        ]
        assert self._ends(records) == {("departed", "resigned")}

    def test_a_death_is_never_a_move(self):
        records = [
            _record(
                "Deceased July 1, 2019",
                chamber="house",
                district=1,
                year=2019,
                name="Derek Stanford",
            ),
            _record(
                "Appointed July 1, 2019 to serve unexpired term",
                chamber="senate",
                district=1,
                year=2017,
                name="Derek Stanford",
            ),
        ]
        assert self._ends(records) == {("departed", "died")}
