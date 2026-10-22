"""Counterpart clipping (#360) — a dated succession moves BOTH sides, or neither."""

from dataclasses import replace
from datetime import date

import pytest

from clearinghouse_domain_legislative.seat_clipping import (
    UNCLIPPED_MULTI_HOLDER_PERIOD,
    UNCLIPPED_NEITHER_DATED,
    CapacityPeriod,
    clip_seat_counterparts,
    clip_seat_families,
)
from clearinghouse_domain_legislative.span_kinds import (
    KIND_COMMITTEE,
    KIND_HOUSE,
    KIND_PARTY,
    KIND_SENATE,
)
from clearinghouse_domain_legislative.tenure_spans import TenureSpan, biennium_end_date
from clearinghouse_domain_legislative.terms import biennium_start_date


def _span(
    member,
    *,
    start,
    end,
    frm,
    to,
    kind=KIND_SENATE,
    disc="4",
    active=False,
    start_basis=None,
    end_basis=None,
    membership_type="principal",
):
    """A span whose basis, unless stated, is what its geometry would have said before
    #501: an edge off its own biennium edge was dated by an event. That reading is the
    fixture's convenience only — the clip reads the basis, and the cases in
    :class:`TestBasisNotGeometry` state one that disagrees with the geometry."""
    if start_basis is None:
        start_basis = "observed" if frm == biennium_start_date(start) else "dated"
    if end_basis is None:
        if to is None:
            end_basis = "open"
        else:
            end_basis = "observed" if to == biennium_end_date(end) else "dated"
    return TenureSpan(
        member_id=member,
        kind=kind,
        discriminator=disc,
        start_biennium=start,
        end_biennium=end,
        valid_from=frm,
        valid_to=to,
        is_active=active,
        start_basis=start_basis,
        end_basis=end_basis,
        membership_type=membership_type,
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

    def test_a_nested_successor_is_the_same_refusal_from_the_other_side(self):
        """The predecessor's own exit is the dated one here, so the pair enters
        the opposite branch — but the geometry is identical (the successor sits
        wholly inside the predecessor's window) and so must the reason be, or the
        residue taxonomy counts one shape under two labels."""
        pred = _span(
            "returner", start="1955-56", end="1957-58", frm=date(1955, 1, 1), to=date(1957, 6, 21)
        )
        succ = _span(
            "interlude", start="1957-58", end="1957-58", frm=date(1957, 1, 1), to=date(1957, 3, 1)
        )
        result = clip_seat_counterparts([pred, succ])
        assert _by_member(result)["interlude"].valid_from == date(1957, 1, 1)
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

    def test_members_are_reported_predecessor_first(self):
        """`member_a` is the earlier tenure whatever order the input arrived in,
        so a reader can tell which side of the handoff each member is on."""
        pred = _span(
            "earlier",
            start="1895-96",
            end="1901-02",
            frm=date(1895, 1, 1),
            to=date(1902, 12, 31),
        )
        succ = _span(
            "later",
            start="1899-00",
            end="1901-02",
            frm=date(1899, 1, 1),
            to=date(1902, 12, 31),
        )
        for spans in ([pred, succ], [succ, pred]):
            (unclipped,) = clip_seat_counterparts(spans).unclipped
            assert (unclipped.member_a, unclipped.member_b) == ("earlier", "later")

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


class TestNoDerivedBoundaryIsEvidence:
    """usa-wa#360 follow-up: a boundary the clip itself moved is DERIVED. Reading
    it back as a stated date on a later pair cascades one clip into another and
    can collapse a tenure to nothing (Donn Charnley, senate LD-44, 1979)."""

    def _ld44_1979(self):
        # North's exit is dated; Charnley opens on the biennium floor; Bradburn
        # opens on the same day North leaves. Three holders, one seat.
        north = _span(
            "north",
            start="1975-76",
            end="1979-80",
            disc="44",
            frm=date(1975, 1, 1),
            to=date(1979, 12, 31),
        )
        charnley = _span(
            "charnley",
            start="1979-80",
            end="1981-82",
            disc="44",
            frm=date(1979, 1, 1),
            to=date(1982, 12, 31),
        )
        bradburn = _span(
            "bradburn",
            start="1979-80",
            end="1981-82",
            disc="44",
            frm=date(1979, 12, 31),
            to=date(1982, 12, 31),
        )
        return north, charnley, bradburn

    def test_the_stated_handoff_still_applies(self):
        result = clip_seat_counterparts(list(self._ld44_1979()))
        assert _by_member(result)["charnley"].valid_from == date(1979, 12, 31)

    def test_the_clipped_start_does_not_then_date_a_third_holder(self):
        """Charnley's new start came from North, not from the roster. Treating it
        as stated evidence closes Bradburn at his own opening day."""
        result = clip_seat_counterparts(list(self._ld44_1979()))
        bradburn = _by_member(result)["bradburn"]
        assert bradburn.valid_from == date(1979, 12, 31)
        assert bradburn.valid_to == date(1982, 12, 31)
        # The assertion that discriminates (CR 141): with Charnley's clipped start
        # read back as a stated date, this pair was CLIPPED — Bradburn closed at his
        # own opening, zero length — and left no residue at all. Read as derived,
        # the pair is refused, and refused for the right reason.
        assert [(u.member_a, u.member_b, u.reason) for u in result.unclipped] == [
            ("charnley", "bradburn", "degenerate")
        ]

    def test_no_span_is_collapsed_to_zero_length(self):
        for span in clip_seat_counterparts(list(self._ld44_1979())).spans:
            assert span.valid_to is None or span.valid_to > span.valid_from


class TestNeverDegenerate:
    def test_a_clip_that_would_empty_the_predecessor_is_refused(self):
        pred = _span(
            "pred", start="1979-80", end="1981-82", frm=date(1979, 12, 31), to=date(1982, 12, 31)
        )
        succ = _span(
            "succ", start="1979-80", end="1981-82", frm=date(1979, 12, 31), to=date(1982, 12, 31)
        )
        # succ's start is dated (not its biennium floor), so branch B applies and
        # would set pred.valid_to = pred.valid_from.
        result = clip_seat_counterparts([pred, succ])
        assert _by_member(result)["pred"].valid_to == date(1982, 12, 31)
        assert [u.reason for u in result.unclipped] == ["degenerate"]

    def test_a_clip_that_would_empty_the_successor_is_refused(self):
        pred = _span(
            "pred", start="1955-56", end="1957-58", frm=date(1955, 1, 1), to=date(1957, 3, 1)
        )
        succ = _span(
            "succ", start="1957-58", end="1957-58", frm=date(1957, 1, 1), to=date(1957, 3, 1)
        )
        result = clip_seat_counterparts([pred, succ])
        assert _by_member(result)["succ"].valid_from == date(1957, 1, 1)
        assert result.unclipped[0].reason == "predecessor_outlives_successor"


class TestMemberMove:
    """usa-wa#282 — one member, two seats: the counterpart rule on the member axis.

    Janéa Holmquist was sworn into Senate LD13 on 2006-12-06 to serve an unexpired
    term. The overlay dates her Senate span there, but her House span keeps its
    quantized 2006-12-31 ceiling, so for 25 days she holds two seats at once — the
    shape `assignments_one_seat_per_member` errors on. The seating is the stated
    date and the ceiling the derived one, so the ceiling yields."""

    def _house(self, **kw):
        base = dict(
            start="2003-04",
            end="2005-06",
            frm=date(2003, 1, 1),
            to=date(2006, 12, 31),
            kind=KIND_HOUSE,
            disc="ld-13-position-1",
        )
        return _span("3430", **{**base, **kw})

    def _senate(self, **kw):
        base = dict(
            start="2007-08", end="2013-14", frm=date(2006, 12, 6), to=date(2014, 12, 31), disc="13"
        )
        return _span("3430", **{**base, **kw})

    def test_the_old_seat_closes_at_the_dated_seating(self):
        house, senate = clip_seat_counterparts([self._house(), self._senate()]).spans
        assert house.valid_to == date(2006, 12, 6)
        assert house.is_active is False
        assert senate.valid_from == date(2006, 12, 6), "the stated side never moves"

    def test_a_quantized_seating_is_no_evidence(self):
        """Neither side dated: the overlap is the builder's, with nothing to clip to."""
        house, senate = clip_seat_counterparts(
            [self._house(), self._senate(frm=date(2005, 1, 1), start="2005-06")]
        ).spans
        assert house.valid_to == date(2006, 12, 31)
        assert senate.valid_from == date(2005, 1, 1)

    def test_a_stated_exit_is_left_alone(self):
        """Both sides dated and still overlapping is the sources disagreeing."""
        house, _ = clip_seat_counterparts(
            [self._house(to=date(2006, 12, 20)), self._senate()]
        ).spans
        assert house.valid_to == date(2006, 12, 20)

    def test_an_open_old_seat_is_left_alone(self):
        """An open seat is the live cohort, which a `vacated` dates (#145) — not
        an inference from geometry."""
        house, _ = clip_seat_counterparts([self._house(to=None, active=True), self._senate()]).spans
        assert house.valid_to is None

    def test_an_old_seat_outliving_the_new_one_is_left_alone(self):
        """The old row runs past the new seat's end, so it is two tenures merged
        (#267) and closing it would discard the second."""
        house, _ = clip_seat_counterparts(
            [self._house(end="2009-10", to=date(2010, 12, 31)), self._senate(to=date(2008, 6, 1))]
        ).spans
        assert house.valid_to == date(2010, 12, 31)

    def test_a_derived_start_is_no_evidence(self):
        """A start the seat pass clips onto a predecessor's exit is an inference,
        and must not close the member's own other seat — the member axis runs
        first, and reads that start while it is still a biennium floor."""
        pred = _span(
            "pred",
            start="2007-08",
            end="2007-08",
            frm=date(2007, 1, 1),
            to=date(2007, 3, 1),
            disc="13",
        )
        senate = self._senate(frm=date(2007, 1, 1))
        house = self._house(end="2007-08", to=date(2008, 12, 31))
        out = clip_seat_counterparts([pred, senate, house]).spans
        assert out[1].valid_from == date(2007, 3, 1), "the seat pass clipped it"
        assert out[2].valid_to == date(2008, 12, 31), "and that clip dates nothing"

    def test_a_successor_seated_later_does_not_strand_the_move(self):
        """The ordinary chamber move: the member leaves the House on 12-06, and
        the House successor is sworn in on 12-20. Clipped on the seat axis first,
        the House ceiling lands on 12-20 and is marked derived, so the member
        axis may no longer touch it — and the member holds two seats for two
        weeks, which `assignments_one_seat_per_member` errors on."""
        succ = _span(
            "x",
            start="2007-08",
            end="2009-10",
            frm=date(2006, 12, 20),
            to=date(2010, 12, 31),
            kind=KIND_HOUSE,
            disc="ld-13-position-1",
        )
        house, senate, _ = clip_seat_counterparts([self._house(), self._senate(), succ]).spans
        assert house.valid_to == senate.valid_from == date(2006, 12, 6)

    def test_the_member_axis_is_idempotent(self):
        """The nightly rebuild clips every run. The closed ceiling is a stated
        date the second time round, so nothing moves again."""
        once = clip_seat_counterparts([self._house(), self._senate()]).spans
        assert clip_seat_counterparts(once).spans == once

    def test_two_members_are_never_paired(self):
        house = self._house()
        other = _span(
            "other", start="2007-08", end="2013-14", frm=date(2006, 12, 6), to=None, disc="13"
        )
        out, _ = clip_seat_counterparts([house, other]).spans
        assert out.valid_to == date(2006, 12, 31)


class TestBasisNotGeometry:
    """usa-wa#501 D4: "stated" reads the span's basis, never its date's geometry."""

    def test_a_dated_seating_on_the_floor_is_stated(self):
        """Pair #3, LD-15: Woodall's real `seated 1957-01-01` lands on his biennium's
        floor. Read geometrically it was quantized, and the pair sat in `neither_dated`
        (#501 correction 5); read by basis, Flanagan closes at it."""
        flanagan = _span(
            "flanagan", start="1943-44", end="1957-58", frm=date(1943, 1, 1), to=date(1958, 12, 31)
        )
        woodall = _span(
            "woodall",
            start="1957-58",
            end="1957-58",
            frm=date(1957, 1, 1),
            to=date(1958, 12, 31),
            start_basis="dated",
        )
        result = clip_seat_counterparts([flanagan, woodall])
        out = _by_member(result)
        assert out["flanagan"].valid_to == date(1957, 1, 1)
        assert out["flanagan"].end_basis == "derived"
        assert result.unclipped == ()

    def test_a_derived_start_off_its_floor_is_no_evidence(self):
        pred = _span(
            "a", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1898, 12, 31)
        )
        succ = _span(
            "b",
            start="1897-98",
            end="1903-04",
            frm=date(1897, 8, 25),
            to=date(1904, 12, 31),
            start_basis="derived",
        )
        result = clip_seat_counterparts([pred, succ])
        assert [u.reason for u in result.unclipped] == [UNCLIPPED_NEITHER_DATED]

    def test_a_clipped_edge_is_derived(self):
        pred = _span(
            "houghton", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        succ = _span(
            "crow", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)
        )
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert (out["crow"].start_basis, out["houghton"].end_basis) == ("derived", "dated")


def _five_holders_1889():
    """LD-19's 1889 Senate: five lawful holders of one role_key, one of them dated."""
    spans = [
        _span(m, start="1889-90", end="1889-90", frm=date(1889, 1, 1), to=date(1890, 12, 31))
        for m in ("jones", "kinnear", "guilfoil", "wood")
    ]
    spans.append(
        _span(
            "rinehart", start="1889-90", end="1889-90", frm=date(1889, 3, 1), to=date(1890, 12, 31)
        )
    )
    return spans


class TestCapacity:
    """usa-wa#501 D4: a seat that lawfully holds more than one principal is not a
    succession for the clip to resolve (#362's latent bug, closed by construction)."""

    CAPACITY = {
        (KIND_SENATE, "4"): (
            CapacityPeriod(date(1889, 1, 1), date(1890, 12, 31), 5),
            CapacityPeriod(date(1891, 1, 1), None, 1),
        )
    }

    def test_without_capacity_the_dated_holder_clips_the_others(self):
        """Today's behaviour, and the hazard: the dated 1889 start rewrites four
        co-senators into predecessors."""
        result = clip_seat_counterparts(_five_holders_1889())
        assert any(s.valid_to == date(1889, 3, 1) for s in result.spans)

    def test_a_multi_holder_period_declines_the_pair(self):
        spans = _five_holders_1889()
        result = clip_seat_counterparts(spans, capacity=self.CAPACITY)
        assert list(result.spans) == spans
        # every pair of the five, C(5,2) — the dated one and the quantized alike
        reasons = {u.reason for u in result.unclipped}
        assert reasons == {UNCLIPPED_MULTI_HOLDER_PERIOD}
        assert len(result.unclipped) == 10

    def test_a_single_holder_period_still_clips(self):
        pred = _span(
            "a", start="1891-92", end="1891-92", frm=date(1891, 1, 1), to=date(1891, 8, 25)
        )
        succ = _span(
            "b", start="1891-92", end="1893-94", frm=date(1891, 1, 1), to=date(1894, 12, 31)
        )
        out = _by_member(clip_seat_counterparts([pred, succ], capacity=self.CAPACITY))
        assert out["b"].valid_from == date(1891, 8, 25)

    def test_a_seat_missing_from_capacity_holds_one(self):
        pred = _span(
            "a", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        succ = _span(
            "b", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)
        )
        other = {(KIND_SENATE, "19"): (CapacityPeriod(date(1889, 1, 1), None, 5),)}
        out = _by_member(clip_seat_counterparts([pred, succ], capacity=other))
        assert out["b"].valid_from == date(1897, 8, 25)

    def test_families_pass_capacity_through(self):
        spans = _five_holders_1889()
        out, unclipped = clip_seat_families({"x": spans}, capacity=self.CAPACITY)
        assert out["x"] == spans
        assert len(unclipped) == 10


class TestMembership:
    """usa-wa#501 D4/D5: a non-principal is invisible to the seat axis."""

    def test_a_substitute_is_never_a_counterpart(self):
        """Lela Kreidler served LD-22 1991-01-14 → 04-18 while Mike Kreidler was on
        military leave. Both edges are dated and his are not: without the membership,
        the clip would close his tenure at her seating."""
        principal = _span(
            "mike", start="1985-86", end="1991-92", frm=date(1985, 1, 1), to=date(1992, 12, 31)
        )
        substitute = _span(
            "lela",
            start="1991-92",
            end="1991-92",
            frm=date(1991, 1, 14),
            to=date(1991, 4, 18),
            membership_type="substitute",
        )
        result = clip_seat_counterparts([principal, substitute])
        assert list(result.spans) == [principal, substitute]
        assert result.unclipped == ()


class TestWindowedEdges:
    """usa-wa#501 D6a: a stated edge known only to a window moves its counterpart to the
    window's FAR bound, so the derived claim (true start ≥ x, true end ≤ x) holds."""

    def test_a_successor_start_moves_to_the_exit_windows_near_edge(self):
        """Wooding, LD-24, "Resigned July 1900": the successor started no earlier than
        July 1 — and possibly later in the month, which stays a may-overlap."""
        pred = _span(
            "wooding",
            start="1899-00",
            end="1899-00",
            frm=date(1899, 1, 1),
            to=date(1900, 7, 31),
            end_basis="dated",
        )
        pred = replace(pred, end_window_start=date(1900, 7, 1))
        succ = _span(
            "smith", start="1899-00", end="1901-02", frm=date(1899, 1, 1), to=date(1902, 12, 31)
        )
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert (out["smith"].valid_from, out["smith"].start_basis) == (date(1900, 7, 1), "derived")

    def test_a_predecessor_end_moves_to_the_seating_windows_far_edge(self):
        """Rasmussen, "Appointed Oct. 1971": his predecessor left no later than Oct 31."""
        pred = _span(
            "a", start="1967-68", end="1971-72", frm=date(1967, 1, 1), to=date(1972, 12, 31)
        )
        succ = _span(
            "rasmussen",
            start="1971-72",
            end="1971-72",
            frm=date(1971, 10, 1),
            to=date(1972, 12, 31),
            start_basis="dated",
        )
        succ = replace(succ, start_window_end=date(1971, 10, 31))
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert (out["a"].valid_to, out["a"].end_basis) == (date(1971, 10, 31), "derived")
        assert out["a"].valid_to_earliest == date(1971, 1, 1)

    def test_a_day_dated_edge_clips_as_before(self):
        pred = _span(
            "houghton", start="1897-98", end="1897-98", frm=date(1897, 1, 1), to=date(1897, 8, 25)
        )
        succ = _span(
            "crow", start="1897-98", end="1903-04", frm=date(1897, 1, 1), to=date(1904, 12, 31)
        )
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert out["crow"].valid_from == date(1897, 8, 25)

    def test_a_member_move_closes_the_old_seat_at_the_new_windows_far_edge(self):
        old = _span(
            "m",
            start="1971-72",
            end="1973-74",
            frm=date(1971, 1, 1),
            to=date(1974, 12, 31),
            kind=KIND_HOUSE,
            disc="ld-1-position-1",
        )
        new = _span(
            "m",
            start="1973-74",
            end="1973-74",
            frm=date(1973, 11, 1),
            to=date(1974, 12, 31),
            start_basis="dated",
        )
        new = replace(new, start_window_end=date(1973, 11, 30))
        out = {s.kind: s for s in clip_seat_counterparts([old, new]).spans}
        assert out[KIND_HOUSE].valid_to == date(1973, 11, 30)

    def test_closing_a_span_clamps_its_own_start_window(self):
        """A predecessor whose start is only an election window, closed by a dated
        seating inside that window: it started no later than it ended. The write once
        failed TenureSpan's window guard (#501 D6a, measured on the corpus)."""
        pred = _span(
            "a",
            start="1977-78",
            end="1977-78",
            frm=date(1977, 5, 17),
            to=date(1978, 12, 31),
            start_basis="dated",
        )
        pred = replace(pred, start_window_end=date(1978, 12, 31))
        succ = _span(
            "b",
            start="1977-78",
            end="1979-80",
            frm=date(1977, 6, 30),
            to=date(1980, 12, 31),
            start_basis="dated",
        )
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert out["a"].valid_to == date(1977, 6, 30)
        assert out["a"].valid_from_latest == date(1977, 6, 30)

    def test_a_day_inside_the_successors_window_opens_it(self):
        """Happy, LD-6: John H. Happy died June 21, 1962 (a day); Marjorie was "Appointed
        1962" (a year). One holder at a time, so she opened no earlier than his death:
        the day wins over the window, and the pair no longer may-overlap."""
        john = _span(
            "john", start="1947-48", end="1961-62", frm=date(1947, 1, 1), to=date(1962, 6, 21)
        )
        marjorie = _span(
            "marjorie",
            start="1961-62",
            end="1961-62",
            frm=date(1962, 1, 1),
            to=date(1962, 12, 31),
            start_basis="dated",
        )
        marjorie = replace(marjorie, start_window_end=date(1962, 12, 31))
        result = clip_seat_counterparts([john, marjorie])
        out = _by_member(result)
        assert (out["marjorie"].valid_from, out["marjorie"].start_basis) == (
            date(1962, 6, 21),
            "derived",
        )
        assert out["john"].valid_to == date(1962, 6, 21)
        assert result.unclipped == ()

    def test_a_day_inside_the_predecessors_window_closes_it(self):
        """LD-48: a resignation dated to January 1967, a successor seated Jan 5."""
        pred = _span(
            "thompson",
            start="1959-60",
            end="1967-68",
            frm=date(1959, 1, 1),
            to=date(1967, 1, 31),
            end_basis="dated",
        )
        pred = replace(pred, end_window_start=date(1967, 1, 1))
        succ = _span(
            "andersen", start="1967-68", end="1971-72", frm=date(1967, 1, 5), to=date(1972, 12, 27)
        )
        out = _by_member(clip_seat_counterparts([pred, succ]))
        assert (out["thompson"].valid_to, out["thompson"].end_basis) == (
            date(1967, 1, 5),
            "derived",
        )

    def test_two_coarse_windows_still_both_stand(self):
        """Neither side is precise enough to move the other: a may-overlap, reported."""
        pred = _span(
            "a",
            start="1975-76",
            end="1977-78",
            frm=date(1975, 1, 1),
            to=date(1977, 6, 30),
            end_basis="dated",
        )
        pred = replace(pred, end_window_start=date(1977, 6, 1))
        succ = _span(
            "b",
            start="1977-78",
            end="1977-78",
            frm=date(1977, 6, 1),
            to=date(1978, 12, 31),
            start_basis="dated",
        )
        succ = replace(succ, start_window_end=date(1977, 6, 30))
        result = clip_seat_counterparts([pred, succ])
        assert [u.reason for u in result.unclipped] == ["both_dated"]
