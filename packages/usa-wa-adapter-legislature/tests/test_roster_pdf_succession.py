"""Roster annotation → dated clauses (#226, epic #219 Phase 2).

Every string here is verbatim from the 2025-06-05 edition. The parser exists to survive ~90
years of clerks' prose, so invented examples would test the wrong thing.
"""

from __future__ import annotations

from datetime import date

import pytest

from usa_wa_adapter_legislature.roster_pdf import succession
from usa_wa_adapter_legislature.roster_pdf.succession import parse_annotation


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
