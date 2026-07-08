"""Unit tests for the merged-span tenure builder (#78, the Phase B core).

Pure function over per-member biennium observations → merged `valid_from..valid_to`
spans. Crafted sequences cover: single term, multi-term contiguous (one span), dormancy
gap (two spans), party/seat change (new span), chamber move (two overlapping spans), the
open current-biennium end, and rollover closing a span.
"""

from __future__ import annotations

from datetime import date

from usa_wa_adapter_legislature.tenure_spans import (
    Observation,
    TenureSpan,
    build_tenure_spans,
)

CURRENT = "2025-26"


def _obs(member_id, kind, disc, *bienniums):
    return [Observation(member_id, kind, disc, b) for b in bienniums]


def _spans(observations, current=CURRENT):
    return build_tenure_spans(observations, current_biennium=current)


def test_single_current_term_is_open_and_active():
    (span,) = _spans(_obs("100", "chamber-senate", "5", "2025-26"))
    assert span.start_biennium == "2025-26" and span.end_biennium == "2025-26"
    assert span.valid_from == date(2025, 1, 1)
    assert span.valid_to is None and span.is_active is True
    assert span.source_id == "100:chamber-senate:5:2025-26"


def test_single_past_term_is_closed():
    (span,) = _spans(_obs("100", "chamber-senate", "5", "2021-22"))
    assert span.valid_from == date(2021, 1, 1)
    assert span.valid_to == date(2022, 12, 31) and span.is_active is False


def test_contiguous_terms_merge_into_one_span():
    span_list = _spans(_obs("100", "chamber-senate", "5", "2021-22", "2023-24", "2025-26"))
    assert len(span_list) == 1
    span = span_list[0]
    assert span.start_biennium == "2021-22" and span.end_biennium == "2025-26"
    assert span.valid_from == date(2021, 1, 1)
    assert span.valid_to is None and span.is_active is True  # reaches current → open
    assert span.source_id == "100:chamber-senate:5:2021-22"  # keyed on tenure start


def test_dormancy_gap_splits_into_two_spans():
    # Served 2017-18, out for 2019-20, back 2021-22 → two spans (a gap breaks tenure).
    span_list = sorted(
        _spans(_obs("100", "chamber-senate", "5", "2017-18", "2021-22")),
        key=lambda s: s.start_biennium,
    )
    assert [s.start_biennium for s in span_list] == ["2017-18", "2021-22"]
    assert span_list[0].valid_to == date(2018, 12, 31) and span_list[0].is_active is False
    assert span_list[1].valid_to == date(2022, 12, 31) and span_list[1].is_active is False


def test_party_switch_is_two_spans():
    # Same kind (party), different discriminator (D then R) → distinct tenures.
    obs = _obs("100", "party", "democratic", "2021-22") + _obs(
        "100", "party", "republican", "2023-24"
    )
    span_list = sorted(_spans(obs), key=lambda s: s.discriminator)
    assert [s.discriminator for s in span_list] == ["democratic", "republican"]
    assert span_list[0].valid_to == date(2022, 12, 31)  # D tenure closed
    assert span_list[1].discriminator == "republican"


def test_chamber_move_yields_two_spans_touching_the_move_biennium():
    # House through 2023-24, Senate from 2023-24 (mid-biennium mover): distinct kinds, both
    # valid in 2023-24.
    obs = _obs("100", "chamber-house", "5:Position 1", "2021-22", "2023-24") + _obs(
        "100", "chamber-senate", "5", "2023-24", "2025-26"
    )
    by_kind = {s.kind: s for s in _spans(obs)}
    assert by_kind["chamber-house"].end_biennium == "2023-24"
    assert by_kind["chamber-house"].valid_to == date(2024, 12, 31)  # left the House
    assert by_kind["chamber-senate"].start_biennium == "2023-24"
    assert by_kind["chamber-senate"].is_active is True  # still in the Senate


def test_distinct_members_and_kinds_are_separate_spans():
    obs = (
        _obs("100", "party", "democratic", "2025-26")
        + _obs("100", "chamber-senate", "5", "2025-26")
        + _obs("200", "party", "republican", "2025-26")
    )
    spans = _spans(obs)
    assert len(spans) == 3
    assert {(s.member_id, s.kind) for s in spans} == {
        ("100", "party"),
        ("100", "chamber-senate"),
        ("200", "party"),
    }


def test_duplicate_observations_collapse():
    # The same (member, kind, disc, biennium) observed twice must not double-count.
    obs = _obs("100", "chamber-senate", "5", "2025-26") * 2
    assert len(_spans(obs)) == 1


def test_output_is_deterministically_ordered():
    obs = _obs("200", "party", "republican", "2025-26") + _obs(
        "100", "chamber-senate", "5", "2025-26"
    )
    a = _spans(obs)
    b = _spans(list(reversed(obs)))
    assert a == b  # order-independent, stable output
    assert all(isinstance(s, TenureSpan) for s in a)
