"""Boundary basis and interval (usa-wa#501 D6, #369 + #360 increment 2).

The basis says WHERE a boundary date came from; the interval says HOW PRECISELY it
is known. Both live on :class:`TenureSpan`, so every writer of a boundary states
its basis and the interval follows from it — never from the date's geometry
(#501 correction 5: 11 operator events land exactly on a biennium edge).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from clearinghouse_domain_legislative.boundary_basis import (
    BASIS_DATED,
    BASIS_DERIVED,
    BASIS_OBSERVED,
    BASIS_OPEN,
    BOUNDARY_BASES,
)
from clearinghouse_domain_legislative.membership_types import (
    MEMBERSHIP_PRINCIPAL,
    MEMBERSHIP_SUBSTITUTE,
    MEMBERSHIP_TYPES,
)
from clearinghouse_domain_legislative.tenure_spans import TenureSpan


def _span(
    *,
    start="1971-72",
    end="1973-74",
    frm=date(1971, 1, 1),
    to: date | None = date(1974, 12, 31),
    **basis,
) -> TenureSpan:
    return TenureSpan(
        member_id="m",
        kind="chamber-senate",
        discriminator="29",
        start_biennium=start,
        end_biennium=end,
        valid_from=frm,
        valid_to=to,
        is_active=to is None,
        **basis,
    )


def test_the_vocabulary_is_closed_and_ordered() -> None:
    """One enum serves both columns (D6); the datapackage declares it in this order."""
    assert BOUNDARY_BASES == ("dated", "derived", "observed", "open")
    assert MEMBERSHIP_TYPES == ("principal", "substitute", "ex_officio", "acting")


def test_a_span_defaults_to_an_observed_principal() -> None:
    span = _span()
    assert (span.start_basis, span.end_basis) == (BASIS_OBSERVED, BASIS_OBSERVED)
    assert span.membership_type == MEMBERSHIP_PRINCIPAL


def test_an_open_span_defaults_to_an_open_end() -> None:
    assert _span(end="2025-26", to=None).end_basis == BASIS_OPEN


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"start_basis": BASIS_OPEN}, "start"),
        ({"start_basis": "term"}, "start"),
        ({"end_basis": "exact"}, "end"),
        ({"end_basis": BASIS_OPEN}, "open"),
        ({"membership_type": "deputy"}, "membership"),
    ],
)
def test_an_impossible_basis_is_refused(fields: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _span(**fields)


def test_an_open_end_needs_an_open_basis() -> None:
    """Invariant 1: `end_basis = open` ⇔ `valid_to is null`. A writer that opens a
    span without saying so fails at the write, not in tonight's build."""
    closed = _span(end_basis=BASIS_DATED, to=date(1973, 3, 1))
    with pytest.raises(ValueError, match="open"):
        replace(closed, valid_to=None, is_active=True)


def test_a_dated_day_is_exact() -> None:
    span = _span(
        frm=date(1971, 10, 4),
        to=date(1973, 3, 19),
        start_basis=BASIS_DATED,
        end_basis=BASIS_DATED,
    )
    assert span.valid_from_latest == date(1971, 10, 4)
    assert span.valid_to_earliest == date(1973, 3, 19)


def test_an_observed_edge_spans_its_attested_biennium() -> None:
    """An observed start means "first attested in this biennium" and an observed end
    "last attested in this one", so each interval is that whole biennium (#360's
    question: an observed end always has `valid_to_earliest < valid_to`)."""
    span = _span()
    assert span.valid_from_latest == date(1972, 12, 31)
    assert span.valid_to_earliest == date(1973, 1, 1)


def test_an_observed_interval_is_clamped_by_the_other_edge() -> None:
    """A one-biennium tenure that ends on a dated day mid-biennium: its start can't
    be later than its end."""
    span = _span(end="1971-72", to=date(1971, 6, 1), end_basis=BASIS_DATED)
    assert span.valid_from_latest == date(1971, 6, 1)


def test_an_observed_end_is_clamped_by_a_late_dated_start() -> None:
    span = _span(start="1973-74", frm=date(1974, 2, 1), start_basis=BASIS_DATED)
    assert span.valid_to_earliest == date(1974, 2, 1)


def test_a_derived_edge_is_a_bound_never_an_exact_date() -> None:
    """Invariant 6: an inference doesn't pose as exact. A clipped start says the true
    start is no earlier than the moved date, and no later than the biennium allows."""
    span = _span(
        frm=date(1971, 8, 9),
        to=date(1973, 5, 1),
        start_basis=BASIS_DERIVED,
        end_basis=BASIS_DERIVED,
    )
    assert span.valid_from_latest == date(1972, 12, 31)
    assert span.valid_to_earliest == date(1973, 1, 1)


def test_a_derived_end_before_its_own_biennium_is_its_own_bound() -> None:
    """A clip can close a span before its end biennium even opens: the bound is the
    moved date itself, never later than the published end."""
    span = _span(to=date(1972, 5, 1), end_basis=BASIS_DERIVED)
    assert span.valid_to_earliest == date(1972, 5, 1)


def test_a_derived_start_before_its_key_biennium_reaches_into_it() -> None:
    """The #282 lookback and the clip both move a start BEFORE its key biennium."""
    span = _span(start="1973-74", frm=date(1972, 12, 12), start_basis=BASIS_DERIVED)
    assert span.valid_from_latest == date(1974, 12, 31)


def test_an_open_end_has_no_interval() -> None:
    span = _span(end="2025-26", to=None)
    assert span.valid_to_earliest is None
    assert span.valid_from_latest == date(1972, 12, 31)


def test_a_substitute_is_a_membership_not_a_basis() -> None:
    span = _span(membership_type=MEMBERSHIP_SUBSTITUTE)
    assert span.membership_type == MEMBERSHIP_SUBSTITUTE
    assert span.source_id == "m:chamber-senate:29:1971-72"


# #501 D6a: a dated edge can be a window — a month, a year, or an election opening a start.


def test_a_dated_window_bounds_the_start() -> None:
    """Rasmussen, LD-29, "Appointed Oct. 1971": dated, but only to the month."""
    span = _span(frm=date(1971, 10, 1), start_basis="dated", start_window_end=date(1971, 10, 31))
    assert (span.valid_from, span.valid_from_latest) == (date(1971, 10, 1), date(1971, 10, 31))


def test_a_dated_window_bounds_the_end() -> None:
    span = _span(to=date(1973, 12, 31), end_basis="dated", end_window_start=date(1973, 12, 1))
    assert (span.valid_to_earliest, span.valid_to) == (date(1973, 12, 1), date(1973, 12, 31))


@pytest.mark.parametrize(
    "fields",
    [
        {"start_window_end": date(1971, 10, 31)},  # observed: no window
        {"start_basis": "dated", "frm": date(1971, 10, 1), "start_window_end": date(1971, 9, 30)},
        {"start_basis": "dated", "frm": date(1971, 10, 1), "start_window_end": date(1975, 1, 1)},
        {"end_window_start": date(1973, 12, 1)},  # observed end: no window
        {"end_basis": "dated", "to": date(1973, 12, 31), "end_window_start": date(1974, 1, 1)},
        {"end_basis": "dated", "to": date(1973, 12, 31), "end_window_start": date(1970, 1, 1)},
    ],
)
def test_a_window_only_sits_on_a_dated_edge_inside_the_span(fields: dict) -> None:
    with pytest.raises(ValueError, match="window"):
        _span(**fields)


def test_moving_a_windowed_edge_must_drop_the_window() -> None:
    """A writer that re-dates a windowed edge restates it whole, or the write fails."""
    windowed = _span(
        frm=date(1971, 10, 1), start_basis="dated", start_window_end=date(1971, 10, 31)
    )
    with pytest.raises(ValueError, match="window"):
        replace(windowed, valid_from=date(1971, 8, 9), start_basis="derived")
