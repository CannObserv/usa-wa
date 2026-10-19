"""Seat capacity from the roster, and the dense `role_periods` it publishes (#501 D1)."""

from datetime import date

from clearinghouse_domain_legislative.seat_clipping import CapacityPeriod
from clearinghouse_domain_legislative.span_kinds import (
    KIND_COMMITTEE,
    KIND_HOUSE,
    KIND_PARTY,
    KIND_SENATE,
)
from usa_wa_pipeline.conformed.capacity import (
    MAX_HOLDERS_KIND,
    MAX_HOLDERS_ROSTER,
    ROLE_PERIOD_COLUMNS,
    bare_multi_member_years,
    role_period_rows,
    seat_capacity,
)


def _row(district, year, name, annotation=None, chamber="senate"):
    return {
        "district": district,
        "chamber": chamber,
        "year": year,
        "order": 1,
        "name": name,
        "party_token": "R",
        "annotation": annotation,
    }


def _assignment(role_key, kind, disc, valid_from, valid_to=None):
    return {
        "role_key": role_key,
        "span_kind": kind,
        "span_discriminator": disc,
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


LD19_1889 = [_row(19, 1889, n) for n in ("Jones", "Kinnear", "Guilfoil", "Wood", "Rinehart")]


def test_a_bare_multi_row_district_year_seats_that_many() -> None:
    """The 1889 shape: five rows, no annotation, one role_key — five seats."""
    assert bare_multi_member_years(LD19_1889) == {19: {1889: 5}}


def test_an_annotation_anywhere_in_the_year_means_one_seat() -> None:
    """Conservative, as the gate always read it: a successor row is a succession."""
    rows = [_row(4, 1897, "Houghton", "Resigned Aug. 25, 1897"), _row(4, 1897, "Crow")]
    assert bare_multi_member_years(rows) == {}


def test_a_tenure_note_in_the_name_is_an_annotation() -> None:
    """Kreidler's leave clause is printed inside his name, not the annotation column
    (#501 correction 9, accepted limit 7): it is still a succession, not a seat."""
    rows = [
        _row(
            22, 1989, "Myron “Mike” Kreidler (On leave of absence for military duty Jan. 8, 1991)"
        ),
        _row(22, 1989, "Lela Kreidler"),
    ]
    assert bare_multi_member_years(rows) == {}


def test_a_name_gloss_is_not_an_annotation() -> None:
    rows = [_row(5, 1889, "Jack (John T.) Dootson"), _row(5, 1889, "Fariss")]
    assert bare_multi_member_years(rows) == {5: {1889: 2}}


def test_the_house_is_out_of_scope() -> None:
    """Pre-1965 House seats were at-large (#230): out of this release (Q1)."""
    rows = [_row(5, 1889, "A", chamber="house"), _row(5, 1889, "B", chamber="house")]
    assert bare_multi_member_years(rows) == {}


def test_the_clip_gets_one_period_per_bare_biennium() -> None:
    assert seat_capacity(LD19_1889) == {
        (KIND_SENATE, "19"): (CapacityPeriod(date(1889, 1, 1), date(1890, 12, 31), 5),)
    }


def test_a_seat_tiles_from_its_first_tenure_with_the_roster_period() -> None:
    rows = role_period_rows(
        [
            _assignment(
                "seat:senate:ld-19", KIND_SENATE, "19", date(1889, 1, 1), date(1890, 12, 31)
            ),
            _assignment("seat:senate:ld-19", KIND_SENATE, "19", date(1891, 1, 1)),
        ],
        LD19_1889,
    )
    assert [tuple(r.values()) for r in rows] == [
        ("seat:senate:ld-19", date(1889, 1, 1), date(1890, 12, 31), 5, MAX_HOLDERS_ROSTER),
        ("seat:senate:ld-19", date(1891, 1, 1), None, 1, MAX_HOLDERS_KIND),
    ]
    assert list(rows[0]) == ROLE_PERIOD_COLUMNS


def test_a_roster_period_mid_history_splits_the_seat_three_ways() -> None:
    rows = role_period_rows(
        [_assignment("seat:senate:ld-2", KIND_SENATE, "2", date(1885, 1, 1))],
        [_row(2, 1889, "A"), _row(2, 1889, "B")],
    )
    assert [(r["valid_from"], r["valid_to"], r["max_holders"]) for r in rows] == [
        (date(1885, 1, 1), date(1888, 12, 31), 1),
        (date(1889, 1, 1), date(1890, 12, 31), 2),
        (date(1891, 1, 1), None, 1),
    ]


def test_a_period_starts_on_the_floor_of_the_earliest_start_not_of_its_key() -> None:
    """A start can sit before its key biennium (#282's lookback, the clip): every
    published instant needs a period (#501 correction 8)."""
    (row,) = role_period_rows(
        [
            _assignment(
                "seat:house:ld-2:position-1", KIND_HOUSE, "ld-2-position-1", date(2014, 1, 17)
            )
        ],
        [],
    )
    assert (row["valid_from"], row["valid_to"], row["max_holders"]) == (date(2013, 1, 1), None, 1)


def test_party_and_committee_roles_have_no_limit() -> None:
    rows = role_period_rows(
        [
            _assignment("party-role:republican", KIND_PARTY, "republican", date(1889, 1, 1)),
            _assignment("committee-role:c1", KIND_COMMITTEE, "c1", date(1991, 1, 1)),
        ],
        LD19_1889,
    )
    assert [(r["role_key"], r["max_holders"], r["max_holders_basis"]) for r in rows] == [
        ("committee-role:c1", None, MAX_HOLDERS_KIND),
        ("party-role:republican", None, MAX_HOLDERS_KIND),
    ]


def test_a_roster_period_before_the_seat_was_ever_held_asserts_nothing() -> None:
    """Accepted limit 4: no capacity before the first published holder."""
    (row,) = role_period_rows(
        [_assignment("seat:senate:ld-19", KIND_SENATE, "19", date(1893, 1, 1))], LD19_1889
    )
    assert (row["valid_from"], row["max_holders"]) == (date(1893, 1, 1), 1)


def test_no_assignments_no_periods() -> None:
    assert role_period_rows([], LD19_1889) == []
