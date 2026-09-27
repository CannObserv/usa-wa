"""Ballot winners keyed to the seat they won (#412 PR B).

The row builder behind the ``seat_winners`` model, which exists so a dbt test
can corroborate an odd-year special against ``assignments`` in SQL. Winner
selection is the SOS normalizers' (imported unchanged, never-guess tiebreak
and all); these pin the plumbing around it and that the seat key is the one
``assignments`` carries.
"""

from clearinghouse_domain_legislative.span_kinds import KIND_HOUSE, KIND_SENATE
from usa_wa_common.seats import house_span_discriminator
from usa_wa_pipeline.conformed.roles import role_for_span
from usa_wa_pipeline.conformed.winners import SEAT_WINNER_SCHEMA, winner_rows

SOURCE = "usa_wa_sos_results"


def _result(date: str, race: str, candidate: str, votes: str | None = "100") -> dict:
    return {
        "election_date": date,
        "race": race,
        "candidate": candidate,
        "party": "(Prefers Democratic Party)",
        "votes": votes,
        "percentage_of_total_votes": None,
        "jurisdiction_name": "Legislative",
        "source": SOURCE,
        "resource_id": f"sos-legresults:{date}",
    }


def test_a_senate_winner_names_the_seat_assignments_carry():
    [row] = winner_rows(
        [
            _result("20251104", "Legislative District 5 - State Senator", "Victoria Hunt", "900"),
            _result("20251104", "Legislative District 5 - State Senator", "Chad Magendanz", "800"),
        ]
    )
    assert row["role_key"] == role_for_span(KIND_SENATE, "5").role_key
    assert (row["election_year"], row["district"], row["qualifier"]) == (2025, 5, None)


def test_a_house_winner_names_its_position_seat():
    [row] = winner_rows(
        [
            _result(
                "20151103", "Legislative District 30 - State Representative Pos. 2", "Teri Hickel"
            ),
        ]
    )
    discriminator = house_span_discriminator(30, "Position 2")
    assert row["role_key"] == role_for_span(KIND_HOUSE, discriminator).role_key
    assert (row["district"], row["qualifier"]) == (30, "Position 2")


def test_only_the_winner_of_each_race_is_emitted():
    rows = winner_rows(
        [
            _result("20171107", "Legislative District 45 - State Senator", "Manka Dhingra", "900"),
            _result(
                "20171107", "Legislative District 45 - State Senator", "Jinyoung Englund", "700"
            ),
            _result("20171107", "Legislative District 7 - State Senator", "Shelly Short"),
        ]
    )
    assert sorted(row["district"] for row in rows) == [7, 45]


def test_a_tie_is_omitted_never_guessed():
    rows = winner_rows(
        [
            _result("20171107", "Legislative District 45 - State Senator", "A Person", "500"),
            _result("20171107", "Legislative District 45 - State Senator", "B Person", "500"),
        ]
    )
    assert rows == []


def test_each_election_is_its_own_cohort():
    """Winners are chosen per wire: two years' candidacies never compete."""
    rows = winner_rows(
        [
            _result("20241105", "Legislative District 5 - State Senator", "Old Winner", "900"),
            _result("20251104", "Legislative District 5 - State Senator", "New Winner", "100"),
        ]
    )
    assert sorted((row["election_year"], row["resource_id"]) for row in rows) == [
        (2024, "sos-legresults:20241105"),
        (2025, "sos-legresults:20251104"),
    ]


def test_every_row_names_its_wire():
    [row] = winner_rows([_result("20251104", "Legislative District 5 - State Senator", "Hunt")])
    assert (row["source"], row["resource_id"]) == (SOURCE, "sos-legresults:20251104")
    assert row["election_date"] == "20251104"


def test_a_row_with_no_election_year_is_skipped():
    """The year keys a cohort to its ballot; a row that cannot supply one cannot
    be attributed to an election (the ``sos_result_wires`` rule)."""
    assert winner_rows([_result("", "Legislative District 5 - State Senator", "Hunt")]) == []


def test_rows_carry_exactly_the_declared_columns():
    rows = winner_rows(
        [
            _result("20251104", "Legislative District 5 - State Senator", "Hunt"),
            _result("20251104", "Legislative District 5 - State Representative Pos. 1", "Rep"),
        ]
    )
    assert rows and all(list(row) == list(SEAT_WINNER_SCHEMA) for row in rows)


def test_no_results_no_winners():
    assert winner_rows([]) == []
