"""Pre-1991 roster → tenure observations (#228 §4/§5) — pure.

The projector turns resolved identities into the same
:class:`~clearinghouse_domain_legislative.tenure_spans.Observation` shapes the sponsor
Phase B emits — ``(member, party, slug, biennium)`` and ``(member, chamber-senate, LD,
biennium)`` — so either builder shape can consume them:

* a Senate term expands from its term-start listing, bounded by the **next listing on the
  seat** (a flat four-year expansion overruns the next occupant 145 times, spec §5) and by
  the 1991 identity floor;
* successor rows refine their start (and predecessors their end) from their own annotation
  dates, quantized to bienniums by the term calendar — residual same-biennium handoff
  overlaps are reported, never silently dropped;
* party follows the seat expansion, split where a change annotation says so (§4), with the
  dated-no-token family inferring the new party from the member's next listing;
* a declined party token (the two power-map#442 adjudications) withholds the party
  observation and tallies the reason; the seat observations build normally (§6).
"""

from __future__ import annotations

import pytest

from clearinghouse_domain_legislative.span_kinds import KIND_PARTY, KIND_SENATE
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_MINTED,
    IDENTITY_WSL,
    RosterIdentity,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.projector import build_pre1991_observations


def _rec(
    name: str,
    year: int,
    *,
    chamber: str = "senate",
    district: int = 1,
    order: int = 1,
    party: str = "D",
    annotation: str | None = None,
) -> RosterRecord:
    return RosterRecord(
        district=district,
        chamber=chamber,
        year=year,
        order=order,
        name=name,
        party_token=party,
        annotation=annotation,
        page_number=1,
    )


def _minted(key: str, *records: RosterRecord) -> RosterIdentity:
    return RosterIdentity(
        disposition=IDENTITY_MINTED,
        fold=key.split(":")[0],
        key=key,
        wsl_member_id=None,
        records=records,
    )


def _joined(member_id: str, *records: RosterRecord) -> RosterIdentity:
    return RosterIdentity(
        disposition=IDENTITY_WSL,
        fold="x",
        key=None,
        wsl_member_id=member_id,
        records=records,
    )


def _obs(projection) -> set[tuple[str, str, str, str]]:
    return {(o.member_id, o.kind, o.discriminator, o.biennium) for o in projection.observations}


def test_senate_term_expands_to_two_bienniums() -> None:
    rows = [_rec("A. Senator", 1899, district=2), _rec("B. Next", 1903, district=2)]
    projection = build_pre1991_observations([_minted("asenator:1899", rows[0])], rows)
    assert (("asenator:1899", KIND_SENATE, "2", "1899-00")) in _obs(projection)
    assert (("asenator:1899", KIND_SENATE, "2", "1901-02")) in _obs(projection)
    assert not any(o.biennium == "1903-04" for o in projection.observations)


def test_next_listing_truncates_the_expansion() -> None:
    """The 145-truncation rule: a listing two years on (redistricting, mid-term
    replacement) bounds the previous term at one biennium."""
    rows = [_rec("A. Senator", 1929, district=3), _rec("B. Successor", 1931, district=3)]
    projection = build_pre1991_observations([_minted("asenator:1929", rows[0])], rows)
    senate = {
        o.biennium
        for o in projection.observations
        if o.kind == KIND_SENATE and o.member_id == "asenator:1929"
    }
    assert senate == {"1929-30"}


def test_expansion_clamps_at_the_identity_floor() -> None:
    """A 1989 Senate term reaches 1992, but 1991+ belongs to the WSL sponsor era — the
    roster asserts nothing at or above the floor."""
    rows = [_rec("A. Senator", 1989, district=4)]
    projection = build_pre1991_observations([_minted("asenator:1989", rows[0])], rows)
    senate = {o.biennium for o in projection.observations if o.kind == KIND_SENATE}
    assert senate == {"1989-90"}


def test_successor_row_starts_at_its_dated_boundary() -> None:
    """Beck's shape: a successor row under the predecessor's term-start year, dated by its
    own annotation — the span opens at the boundary's biennium, not the row year's."""
    rows = [
        _rec("B. Gardner", 1971, district=26, annotation="Resigned Dec. 13, 1973"),
        _rec(
            "C. W. Beck",
            1971,
            district=26,
            order=2,
            annotation="Appointed Feb. 11, 1974 to serve 1974 Ex. S.",
        ),
        _rec("C. W. Beck", 1975, district=26),
    ]
    projection = build_pre1991_observations(
        [_minted("bgardner:1971", rows[0]), _minted("cwbeck:1971", rows[1], rows[2])], rows
    )
    beck = {
        o.biennium
        for o in projection.observations
        if o.kind == KIND_SENATE and o.member_id == "cwbeck:1971"
    }
    assert "1973-74" in beck
    assert "1971-72" not in beck


def test_predecessor_row_ends_at_its_dated_boundary() -> None:
    """An early-in-term departure drops the bienniums after its boundary's: a resignation
    in January 1972 ends the coverage at 1971-72."""
    rows = [
        _rec("Q. Quitter", 1971, district=7, annotation="Resigned January 10, 1972"),
        _rec("R. Next", 1975, district=7),
    ]
    projection = build_pre1991_observations([_minted("qquitter:1971", rows[0])], rows)
    senate = {
        o.biennium
        for o in projection.observations
        if o.kind == KIND_SENATE and o.member_id == "qquitter:1971"
    }
    assert senate == {"1971-72"}


def test_undated_handoff_overlap_is_reported() -> None:
    """Two rows sharing a term-start year with no dates genuinely overlap at biennium
    grain — reported for the oracle, never silently dropped or silently kept."""
    rows = [
        _rec("A. Holder", 1903, district=9),
        _rec("B. Successor", 1903, district=9, order=2),
        _rec("C. Later", 1907, district=9),
    ]
    projection = build_pre1991_observations(
        [_minted("aholder:1903", rows[0]), _minted("bsuccessor:1903", rows[1])], rows
    )
    assert projection.seat_overlaps  # at least the shared (senate, 9) bienniums


def test_house_row_emits_party_only() -> None:
    """House seats are #229/#230's scope; the party observation still builds, one
    biennium per listing."""
    rows = [_rec("H. Member", 1985, chamber="house", district=30, party="R")]
    projection = build_pre1991_observations([_minted("hmember:1985", rows[0])], rows)
    assert _obs(projection) == {("hmember:1985", KIND_PARTY, "republican", "1985-86")}


def test_party_follows_the_senate_expansion() -> None:
    rows = [_rec("A. Senator", 1899, district=2, party="R"), _rec("B. Next", 1903, district=2)]
    projection = build_pre1991_observations([_minted("asenator:1899", rows[0])], rows)
    party = {o.biennium for o in projection.observations if o.kind == KIND_PARTY}
    assert party == {"1899-00", "1901-02"}


def test_change_annotation_splits_the_party_mid_term() -> None:
    """Landon (spec §4): republican 1911-12, progressive 1913-16, republican 1917-18 —
    each term-start row's token holds until its own change year."""
    rows = [
        _rec(
            "Daniel Landon",
            1911,
            district=32,
            party="R",
            annotation="(Changed party affiliation, 1913) Prog.",
        ),
        _rec(
            "Daniel Landon",
            1915,
            district=32,
            party="Prog.",
            annotation="(Changed party affiliation, 1917) R",
        ),
        _rec("Someone Else", 1919, district=32),
    ]
    projection = build_pre1991_observations([_minted("daniellandon:1911", *rows[:2])], rows)
    party = {(o.discriminator, o.biennium) for o in projection.observations if o.kind == KIND_PARTY}
    assert party == {
        ("republican", "1911-12"),
        ("progressive", "1913-14"),
        ("progressive", "1915-16"),
        ("republican", "1917-18"),
    }


def test_dated_change_without_token_takes_the_next_listing_party() -> None:
    """von Reichbauer: the annotation dates the change and says nothing about the new
    party — the member's next listing does."""
    rows = [
        _rec(
            "P. von Reichbauer",
            1979,
            district=30,
            party="D",
            annotation="Changed party affiliation February 13, 1981",
        ),
        _rec("P. von Reichbauer", 1983, district=30, party="R"),
        _rec("Someone Else", 1987, district=30),
    ]
    projection = build_pre1991_observations([_minted("pvonreichbauer:1979", *rows[:2])], rows)
    party = {(o.discriminator, o.biennium) for o in projection.observations if o.kind == KIND_PARTY}
    assert ("democratic", "1979-80") in party
    assert ("republican", "1981-82") in party


def test_declined_party_withholds_the_observation_and_names_its_member() -> None:
    """Welty's 1899 ``Cit.`` (§6): the seat span builds; the party assignment is withheld
    with its member attributed, so the residue is actionable rather than a bare count
    (CR #77)."""
    rows = [_rec("G. Welty", 1899, chamber="house", district=1, party="Cit.")]
    projection = build_pre1991_observations([_minted("gwelty:1899", rows[0])], rows)
    assert not any(o.kind == KIND_PARTY for o in projection.observations)
    (decline,) = projection.declined_parties
    assert decline.member == "gwelty:1899"
    assert decline.reason == "ballot_label"
    assert decline.token == "Cit."


def test_wsl_joined_identity_uses_the_member_id() -> None:
    """A joined identity's observations key on the WSL member id, so its pre-1991 spans
    extend the same Person the sponsor builder owns."""
    rows = [_rec("A. Rasmussen", 1987, district=29, party="D")]
    projection = build_pre1991_observations([_joined("577", rows[0])], rows)
    assert ("577", KIND_SENATE, "29", "1987-88") in _obs(projection)
    assert ("577", KIND_PARTY, "democratic", "1987-88") in _obs(projection)


def test_unparseable_change_is_tallied() -> None:
    rows = [
        _rec(
            "X. Odd",
            1955,
            district=11,
            party="D",
            annotation="Changed party affiliation at some point, allegedly",
        )
    ]
    projection = build_pre1991_observations([_minted("xodd:1955", rows[0])], rows)
    assert projection.unresolved_changes


def test_zero_coverage_rows_are_reported_with_a_reason() -> None:
    """A member who died between election and swearing-in never sat the term — zero
    coverage is the *right* answer, but it must be visible (CR #74)."""
    rows = [
        _rec("J. Brain", 1951, chamber="house", district=13, annotation="Deceased Dec. 18, 1950"),
    ]
    projection = build_pre1991_observations([_minted("jbrain:1951", rows[0])], rows)
    assert projection.observations == ()
    (uncovered,) = projection.uncovered_rows
    assert uncovered.member == "jbrain:1951"
    assert uncovered.reason == "ended_before_term"


def test_post_floor_start_is_reported_as_floor_scoped() -> None:
    """A 1989-listed successor appointed in 1991 belongs to the WSL sponsor era; the row is
    reported rather than silently skipped (CR #74)."""
    rows = [
        _rec("S. Sumner", 1989, district=28, order=2, annotation="Appointed February 12, 1992"),
    ]
    projection = build_pre1991_observations([_minted("ssumner:1989", rows[0])], rows)
    assert projection.observations == ()
    (uncovered,) = projection.uncovered_rows
    assert uncovered.reason == "starts_at_floor"


def test_identity_without_member_or_key_raises() -> None:
    """Both id fields empty is invalid by construction — fail loudly, never key a span on
    a bare fold (CR #78)."""
    broken = RosterIdentity(
        disposition=IDENTITY_MINTED,
        fold="x",
        key=None,
        wsl_member_id=None,
        records=(_rec("X", 1901),),
    )
    with pytest.raises(ValueError, match="neither"):
        build_pre1991_observations([broken], [])
