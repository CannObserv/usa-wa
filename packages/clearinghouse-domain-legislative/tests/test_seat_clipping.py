"""Counterpart clipping (#360) — a dated succession moves BOTH sides, or neither."""

from datetime import date

import pytest

from clearinghouse_domain_legislative.seat_clipping import (
    clip_seat_counterparts,
    clip_seat_families,
)
from clearinghouse_domain_legislative.span_kinds import (
    KIND_COMMITTEE,
    KIND_HOUSE,
    KIND_PARTY,
    KIND_SENATE,
)
from clearinghouse_domain_legislative.tenure_spans import TenureSpan


def _span(member, *, start, end, frm, to, kind=KIND_SENATE, disc="4", active=False):
    return TenureSpan(
        member_id=member,
        kind=kind,
        discriminator=disc,
        start_biennium=start,
        end_biennium=end,
        valid_from=frm,
        valid_to=to,
        is_active=active,
    )


def _by_member(result):
    return {s.member_id: s for s in result.spans}


class TestClipSuccessorStart:
    """The Houghton/Crow shape: the predecessor's exit is dated, the successor
    still opens on its own biennium floor."""

    def test_successor_opens_at_the_dated_exit(self):
        pred = _span(
            "houghton", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        succ = _span(
            "crow", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)
        )
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert out["crow"].valid_from == date(1897, 8, 25)
        assert out["houghton"].valid_to == date(1897, 8, 25)

    def test_shared_boundary_is_not_an_overlap(self):
        """The gate excludes `a.valid_to = b.valid_from`, so the clip must land
        exactly there rather than a day either side."""
        pred = _span(
            "a", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        succ = _span(
            "b", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)
        )
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert out["a"].valid_to == out["b"].valid_from

    def test_refuses_when_the_clip_leaves_the_successor_biennium(self):
        """A successor the roster listed two biennia before the predecessor's
        dated exit is a contradiction, not a quantization artifact."""
        pred = _span(
            "a", start="1967-68", end="1973-74", frm=date(1967, 1, 1), to=date(1973, 7, 19)
        )
        succ = _span(
            "b", start="1971-72", end="1981-82", frm=date(1971, 1, 1), to=date(1982, 12, 31)
        )
        result = clip_seat_counterparts([pred, succ])
        assert _by_member(result)["b"].valid_from == date(1971, 1, 1)
        assert [u.reason for u in result.unclipped] == ["start_leaves_biennium"]


class TestClipPredecessorEnd:
    """The Thompson/Andersen shape: the successor's seating is dated, the
    predecessor still runs to its own biennium ceiling."""

    def test_predecessor_closes_at_the_dated_seating(self):
        pred = _span(
            "thompson",
            start="1959-60",
            end="1969-70",
            frm=date(1959, 1, 1),
            to=date(1970, 12, 31),
            active=True,
        )
        succ = _span(
            "andersen", start="1967-68", end="1971-72", frm=date(1967, 1, 5), to=date(1972, 12, 27)
        )
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert out["thompson"].valid_to == date(1967, 1, 5)
        assert out["thompson"].is_active is False

    def test_refuses_when_the_predecessor_outlives_the_successor(self):
        """usa-wa#267's hazard: the predecessor returned, so the span is two
        tenures merged and clipping it discards the second."""
        pred = _span(
            "returner", start="1977-78", end="1991-92", frm=date(1977, 1, 1), to=date(1992, 12, 31)
        )
        succ = _span(
            "interlude",
            start="1977-78",
            end="1979-80",
            frm=date(1977, 7, 12),
            to=date(1980, 12, 31),
        )
        result = clip_seat_counterparts([pred, succ])
        assert _by_member(result)["returner"].valid_to == date(1992, 12, 31)
        assert [u.reason for u in result.unclipped] == ["predecessor_outlives_successor"]

    def test_an_open_predecessor_outlives_a_closed_successor(self):
        """`valid_to is None` reads as unbounded, not as a quantized ceiling."""
        pred = _span(
            "sitting", start="1977-78", end="2025-26", frm=date(1977, 1, 1), to=None, active=True
        )
        succ = _span(
            "interlude",
            start="1977-78",
            end="1979-80",
            frm=date(1977, 7, 12),
            to=date(1980, 12, 31),
        )
        result = clip_seat_counterparts([pred, succ])
        assert _by_member(result)["sitting"].valid_to is None
        assert [u.reason for u in result.unclipped] == ["predecessor_outlives_successor"]


class TestRefusals:
    def test_neither_side_dated_is_left_for_the_gate(self):
        a = _span("a", start="1895-96", end="1901-02", frm=date(1895, 1, 1), to=date(1902, 12, 31))
        b = _span("b", start="1899-00", end="1901-02", frm=date(1899, 1, 1), to=date(1902, 12, 31))
        result = clip_seat_counterparts([a, b])
        assert result.spans == (a, b)
        assert [u.reason for u in result.unclipped] == ["neither_dated"]

    def test_both_sides_dated_is_an_upstream_contradiction(self):
        a = _span("a", start="1957-58", end="1975-76", frm=date(1957, 1, 1), to=date(1977, 6, 21))
        b = _span("b", start="1957-58", end="1959-60", frm=date(1957, 6, 6), to=date(1960, 12, 31))
        result = clip_seat_counterparts([a, b])
        assert result.spans == (a, b)
        assert [u.reason for u in result.unclipped] == ["both_dated"]

    def test_reports_the_seat_and_both_members(self):
        a = _span(
            "a",
            start="1895-96",
            end="1901-02",
            disc="24",
            frm=date(1895, 1, 1),
            to=date(1902, 12, 31),
        )
        b = _span(
            "b",
            start="1899-00",
            end="1901-02",
            disc="24",
            frm=date(1899, 1, 1),
            to=date(1902, 12, 31),
        )
        (unclipped,) = clip_seat_counterparts([a, b]).unclipped
        assert (unclipped.kind, unclipped.discriminator) == (KIND_SENATE, "24")
        assert (unclipped.member_a, unclipped.member_b) == ("a", "b")


class TestScope:
    @pytest.mark.parametrize("kind", [KIND_PARTY, KIND_COMMITTEE])
    def test_multi_holder_kinds_are_untouched(self, kind):
        """Party and committee seats are legitimately multi-holder — the
        invariant is stated by KIND, exactly as the gate scopes it."""
        pred = _span(
            "a",
            start="1897-98",
            end="1897-98",
            kind=kind,
            frm=date(1897, 1, 1),
            to=date(1897, 8, 25),
        )
        succ = _span(
            "b",
            start="1897-98",
            end="1903-04",
            kind=kind,
            frm=date(1897, 1, 1),
            to=date(1904, 12, 31),
        )
        result = clip_seat_counterparts([pred, succ])
        assert result.spans == (pred, succ)
        assert result.unclipped == ()

    def test_house_positions_are_distinct_seats(self):
        """Position 1 and Position 2 are two seats, not one shared by two."""
        a = _span(
            "a",
            start="2003-04",
            end="2003-04",
            kind=KIND_HOUSE,
            disc="ld-2-position-1",
            frm=date(2003, 1, 1),
            to=date(2003, 5, 1),
        )
        b = _span(
            "b",
            start="2003-04",
            end="2004-05",
            kind=KIND_HOUSE,
            disc="ld-2-position-2",
            frm=date(2003, 1, 1),
            to=date(2004, 12, 31),
        )
        result = clip_seat_counterparts([a, b])
        assert result.spans == (a, b)
        assert result.unclipped == ()

    def test_one_member_holding_a_seat_twice_is_not_a_conflict(self):
        a = _span(
            "same", start="1957-58", end="1959-60", frm=date(1957, 1, 1), to=date(1960, 12, 31)
        )
        b = _span(
            "same", start="1977-78", end="1979-80", frm=date(1977, 1, 1), to=date(1980, 12, 31)
        )
        result = clip_seat_counterparts([a, b])
        assert result.spans == (a, b)
        assert result.unclipped == ()

    def test_non_overlapping_tenures_are_untouched(self):
        a = _span("a", start="1957-58", end="1959-60", frm=date(1957, 1, 1), to=date(1960, 12, 31))
        b = _span("b", start="1961-62", end="1963-64", frm=date(1961, 1, 1), to=date(1964, 12, 31))
        result = clip_seat_counterparts([a, b])
        assert result.spans == (a, b)
        assert result.unclipped == ()


class TestContract:
    def test_order_and_length_are_preserved(self):
        """`clip_seat_families` re-splits the union by slicing, so the returned
        sequence must be positionally identical to the input."""
        spans = [
            _span("b", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)),
            _span(
                "p",
                start="1997-98",
                end="1997-98",
                kind=KIND_PARTY,
                disc="democrat",
                frm=date(1997, 1, 1),
                to=date(1998, 12, 31),
            ),
            _span("a", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)),
        ]
        result = clip_seat_counterparts(spans)
        assert [s.member_id for s in result.spans] == ["b", "p", "a"]
        assert len(result.spans) == len(spans)

    def test_is_idempotent(self):
        pred = _span(
            "a", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        succ = _span(
            "b", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)
        )
        once = clip_seat_counterparts([pred, succ])
        twice = clip_seat_counterparts(list(once.spans))
        assert twice.spans == once.spans
        assert twice.unclipped == ()

    def test_the_input_spans_are_not_mutated(self):
        pred = _span(
            "a", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        succ = _span(
            "b", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)
        )
        clip_seat_counterparts([pred, succ])
        assert succ.valid_from == date(1897, 1, 1)

    def test_a_three_holder_seat_clips_each_handoff(self):
        first = _span(
            "first", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        second = _span(
            "second", start="1897-98", end="1899-00", frm=date(1897, 1, 1), to=date(1899, 4, 2)
        )
        third = _span(
            "third", start="1899-00", end="1901-02", frm=date(1899, 1, 1), to=date(1902, 12, 31)
        )
        out = _by_member(clip_seat_counterparts([first, second, third]))
        assert out["second"].valid_from == date(1897, 8, 25)
        assert out["third"].valid_from == date(1899, 4, 2)


class TestClipSeatFamilies:
    """The union seam: one seat's two holders come from different builders."""

    def test_a_handoff_across_two_families_is_clipped(self):
        wsl = _span(
            "100", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        roster = _span(
            "crow:1897", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)
        )
        out, unclipped = clip_seat_families({"wsl": [wsl], "roster": [roster]})
        assert out["roster"][0].valid_from == date(1897, 8, 25)
        assert out["wsl"][0].valid_to == date(1897, 8, 25)
        assert unclipped == ()

    def test_each_family_keeps_its_own_spans_in_order(self):
        a = _span("a", start="1997-98", end="1997-98", frm=date(1997, 1, 1), to=date(1998, 12, 31))
        b = _span(
            "b",
            start="1999-00",
            end="1999-00",
            disc="7",
            frm=date(1999, 1, 1),
            to=date(2000, 12, 31),
        )
        c = _span(
            "c",
            start="2001-02",
            end="2001-02",
            disc="9",
            frm=date(2001, 1, 1),
            to=date(2002, 12, 31),
        )
        out, _ = clip_seat_families({"one": [a, b], "two": [c]})
        assert [s.member_id for s in out["one"]] == ["a", "b"]
        assert [s.member_id for s in out["two"]] == ["c"]

    def test_an_empty_family_survives_the_round_trip(self):
        a = _span("a", start="1997-98", end="1997-98", frm=date(1997, 1, 1), to=date(1998, 12, 31))
        out, _ = clip_seat_families({"one": [a], "empty": []})
        assert out["empty"] == []
        assert [s.member_id for s in out["one"]] == ["a"]
