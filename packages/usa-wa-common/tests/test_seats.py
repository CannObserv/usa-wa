"""Unit tests for WA seat keying (#189).

Cases moved verbatim from `usa-wa-adapter-pdc/tests/test_positions.py`; the name-folding half
went to `test_names.py` and the PDC identifier half stayed in the adapter.
"""

from __future__ import annotations

import pytest

from usa_wa_common.seats import (
    canonical_position,
    house_seat_role_source_id,
    house_span_discriminator,
    parse_house_span_discriminator,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", "Position 1"), ("2", "Position 2"), (" 1 ", "Position 1"), (1, "Position 1")],
)
def test_canonical_position_maps_to_qualifier(raw, expected) -> None:
    assert canonical_position(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", None, "0", "3", "abc"])
def test_canonical_position_rejects_non_house_positions(raw) -> None:
    assert canonical_position(raw) is None


def test_house_seat_role_source_id_is_deterministic_per_ld_position() -> None:
    a = house_seat_role_source_id(42, "Position 1")
    assert a == house_seat_role_source_id(42, "Position 1")
    assert a != house_seat_role_source_id(42, "Position 2")
    assert a != house_seat_role_source_id(7, "Position 1")


def test_house_span_discriminator_round_trips() -> None:
    disc = house_span_discriminator(5, "Position 1")
    assert ":" not in disc  # colon-free so the 4-part span source_id stays parseable
    assert disc == "ld-5-position-1"
    assert parse_house_span_discriminator(disc) == (5, "Position 1")
    # distinct seats yield distinct discriminators
    assert house_span_discriminator(5, "Position 2") != disc
    assert house_span_discriminator(6, "Position 1") != disc
