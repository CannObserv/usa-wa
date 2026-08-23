"""Pre-1991 roster identity resolution (#228) — pure, over member-year records.

The rules under test are the ones the design spec settled and the #252-corrected corpus
calibrated:

* the identity fold strips what is not a name — position suffixes (``– 19B``), parenthetical
  segments (``(Mrs. Joseph E.)``), quoted nicknames (``“L.L.”``), honorifics — because each of
  those splits a real person's tenure into two folds when the source prints them
  inconsistently;
* grouping is by fold, modulo a **checked-in adjudication table** (aliases and splits) —
  never a heuristic merge or split, because the measured "contradictions" are almost all
  chamber movers (the roster indexes by term-start year, so a mid-term successor legitimately
  appears under two seats in one session year);
* a group whose consecutive listing years gap more than :data:`WIDE_GAP_YEARS` is **refused
  with a tally** unless an adjudication splits it — the evidence is odd, not contradictory,
  and a silent merge asserts a false identity;
* a fold crossing the 1991 floor joins the **existing WSL member** — seat-scoped surname
  match, then the #240 given-name-initial guard, then the year-corroboration tie-breaker
  (**at least two** distinct years and strictly more than any rival), then the adjudication
  table; never a silent mint of a duplicate Person.
"""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.roster_pdf.identity import (
    CORROBORATION_FLOOR,
    IDENTITY_ALIASES,
    IDENTITY_MINTED,
    IDENTITY_SPLITS,
    IDENTITY_WSL,
    JOIN_ADJUDICATIONS,
    REFUSED_JOIN_AMBIGUOUS,
    REFUSED_JOIN_UNRESOLVED,
    REFUSED_WIDE_GAP,
    WIDE_GAP_YEARS,
    IdentityReport,
    RefusedIdentity,
    identity_fold,
    identity_seatings,
    resolve_identities,
    strip_position_suffix,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.resolve import Seating


def _rec(
    name: str,
    year: int,
    *,
    chamber: str = "house",
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


# --- the identity fold -------------------------------------------------------


def test_fold_strips_position_suffix() -> None:
    assert identity_fold("Bob Basich – 19B") == identity_fold("Bob Basich")


def test_strip_position_suffix_is_public() -> None:
    """The suffix stripper is the display-name minter's too (CR #88) — one public
    helper, so ``persons.py`` never reaches for a private regex."""
    assert strip_position_suffix("Bob Basich – 19B") == "Bob Basich"
    assert strip_position_suffix("Bob Basich") == "Bob Basich"
    assert strip_position_suffix("Margaret Hurley") == "Margaret Hurley"


def test_fold_strips_parenthetical_segments() -> None:
    assert identity_fold("Margaret (Mrs. Joseph E.) Hurley") == identity_fold("Margaret Hurley")


def test_fold_strips_quoted_nicknames() -> None:
    assert identity_fold("Linneus Lincoln “L.L.” Westfall") == identity_fold(
        "Linneus Lincoln Westfall"
    )


def test_fold_drops_honorifics() -> None:
    assert identity_fold("Mrs. Jurie B. Smith") == identity_fold("Jurie B. Smith")


def test_fold_keeps_generational_suffixes() -> None:
    """``Jr`` distinguishes real people; only honorifics are noise."""
    assert identity_fold("Bill Day, Jr") != identity_fold("Bill Day")


def test_fold_is_the_common_fold_after_cleanup() -> None:
    assert identity_fold("John L. O’Brien") == "johnlobrien"


# --- grouping and minting ----------------------------------------------------


def test_non_crossing_group_mints_with_first_year_key() -> None:
    report = resolve_identities(
        [_rec("A. B. Carver", 1899), _rec("A. B. Carver", 1901)], seatings=()
    )
    (identity,) = report.identities
    assert identity.disposition == IDENTITY_MINTED
    assert identity.key == "abcarver:1899"
    assert len(identity.records) == 2
    assert report.refused == ()


def test_alias_variants_group_as_one_identity() -> None:
    """The checked-in alias table joins the source's spelling variants — the key comes from
    the canonical fold and the earliest record of the merged group."""
    report = resolve_identities(
        [
            _rec("Philip McDonough", 1925, district=25),
            _rec("Phillip McDonough", 1931, district=25),
        ],
        seatings=(),
        aliases={"phillipmcdonough": "philipmcdonough"},
    )
    (identity,) = report.identities
    assert identity.key == "philipmcdonough:1925"
    assert len(identity.records) == 2


def test_split_adjudication_partitions_at_the_boundary_year() -> None:
    """An adjudicated split yields two identities, each keyed by its *own* group's earliest
    session year — never the whole fold-group's minimum (spec §1)."""
    report = resolve_identities(
        [
            _rec("Elmer E. Johnston", 1899, district=44, party="P.P."),
            _rec("Elmer E. Johnston", 1947, district=6, party="R"),
            _rec("Elmer E. Johnston", 1949, district=6, party="R"),
        ],
        seatings=(),
        splits={"elmerejohnston": 1947},
    )
    keys = sorted(i.key for i in report.identities)
    assert keys == ["elmerejohnston:1899", "elmerejohnston:1947"]
    by_key = {i.key: i for i in report.identities}
    assert len(by_key["elmerejohnston:1899"].records) == 1
    assert len(by_key["elmerejohnston:1947"].records) == 2


def test_wide_gap_without_adjudication_is_refused_with_subject() -> None:
    """Odd evidence refuses with an actionable subject (oracle item 2) — never a silent
    merge, never a heuristic split."""
    report = resolve_identities(
        [
            _rec("Victor Zednick", 1917, district=43),
            _rec("Victor Zednick", 1943, chamber="senate", district=36),
        ],
        seatings=(),
    )
    assert report.identities == ()
    (refusal,) = report.refused
    assert refusal.reason == REFUSED_WIDE_GAP
    assert refusal.fold == "victorzednick"
    assert "1917" in refusal.detail and "1943" in refusal.detail
    assert len(refusal.records) == 2


def test_chamber_mover_is_one_identity() -> None:
    """The roster indexes by term-start year, so a mid-term chamber mover appears under two
    seats in one session year. That is one career, not a contradiction — the measured
    corpus has 44 of these and roughly zero true simultaneities."""
    report = resolve_identities(
        [
            _rec("Ted Haley", 1975, district=28),
            _rec("Ted Haley", 1977, district=28),
            _rec("Ted Haley", 1977, chamber="senate", district=28),
        ],
        seatings=(),
    )
    (identity,) = report.identities
    assert identity.disposition == IDENTITY_MINTED
    assert identity.key == "tedhaley:1975"
    assert len(identity.records) == 3


# --- the 1991 join -----------------------------------------------------------


def _wsl(
    member_id: str,
    year: int,
    surname: str,
    given: str = "",
    *,
    district: int = 5,
    chamber: str = "house",
) -> Seating:
    return Seating(
        member_id=member_id,
        chamber=chamber,
        district=district,
        year=year,
        surname=surname,
        given_name=given,
    )


def test_crossing_name_joins_the_wsl_member_when_the_guard_passes() -> None:
    report = resolve_identities(
        [
            _rec("Jane Doe", 1985, district=5),
            _rec("Jane Doe", 1991, district=5),
        ],
        seatings=[_wsl("42", 1991, "Doe", "Jane")],
    )
    (identity,) = report.identities
    assert identity.disposition == IDENTITY_WSL
    assert identity.wsl_member_id == "42"
    assert identity.key is None
    # the identity carries the pre-floor records only; 1991+ rows are WSL's own era
    assert [r.year for r in identity.records] == [1985]


def test_guard_failure_falls_to_corroboration_and_the_floor_binds() -> None:
    """The Grant shape: guard rejects the formal↔nickname pair in both directions, but the
    same member id corroborated across two-plus distinct years and ahead of every rival is
    accepted. A single uncontested year is NOT — that is the #240 shape (1 > 0)."""
    grant = [
        _rec("William A. Grant", 1985, district=16),
        _rec("William A. Grant", 1991, district=16),
        _rec("William A. Grant", 1993, district=16),
    ]
    # two distinct years for member 157, zero rivals -> accepted
    seatings = [
        _wsl("157", 1991, "Grant", "Bill", district=16),
        _wsl("157", 1993, "Grant", "Bill", district=16),
    ]
    report = resolve_identities(grant, seatings=seatings)
    (identity,) = report.identities
    assert identity.disposition == IDENTITY_WSL
    assert identity.wsl_member_id == "157"

    # one distinct year only -> refused by the floor, with the candidate named
    report = resolve_identities(grant[:2], seatings=seatings[:1])
    assert report.identities == ()
    (refusal,) = report.refused
    assert refusal.reason == REFUSED_JOIN_UNRESOLVED
    assert "157" in refusal.detail


def test_corroboration_must_beat_every_rival_strictly() -> None:
    """A tie between rejected candidates stays refused — corroboration is a tie-breaker,
    not a coin flip."""
    rows = [
        _rec("Frank Hansen", 1985, district=13),
        _rec("Frank Hansen", 1991, district=13),
        _rec("Frank Hansen", 1993, district=13),
    ]
    seatings = [
        _wsl("168", 1991, "Hansen", "Georgia", district=13),
        _wsl("168", 1993, "Hansen", "Georgia", district=13),
        _wsl("169", 1991, "Hansen", "Zoe", district=13),
        _wsl("169", 1993, "Hansen", "Zoe", district=13),
    ]
    report = resolve_identities(rows, seatings=seatings)
    assert report.identities == ()
    (refusal,) = report.refused
    assert refusal.reason == REFUSED_JOIN_UNRESOLVED


def test_join_adjudication_resolves_to_the_named_member() -> None:
    report = resolve_identities(
        [
            _rec("Bill Day, Jr", 1985, district=3),
            _rec("Bill Day, Jr", 1991, district=3),
        ],
        seatings=[_wsl("103", 1991, "Day", "William", district=3)],
        adjudications={"billdayjr": "103"},
    )
    (identity,) = report.identities
    assert identity.disposition == IDENTITY_WSL
    assert identity.wsl_member_id == "103"


def test_join_adjudication_none_mints_a_roster_person() -> None:
    """The Galloway shape: crossing in the roster, absent from the WSL space entirely —
    adjudicated to mint rather than left refused forever."""
    report = resolve_identities(
        [
            _rec("Shirley Galloway", 1979, district=49),
            _rec("Shirley Galloway", 1993, chamber="senate", district=17),
        ],
        seatings=(),
        adjudications={"shirleygalloway": None},
    )
    (identity,) = report.identities
    assert identity.disposition == IDENTITY_MINTED
    assert identity.key == "shirleygalloway:1979"
    assert [r.year for r in identity.records] == [1979]


def test_two_compatible_members_refuse_as_ambiguous() -> None:
    report = resolve_identities(
        [
            _rec("J. Smith", 1989, district=7),
            _rec("J. Smith", 1991, district=7),
        ],
        seatings=[
            _wsl("1", 1991, "Smith", "John", district=7),
            _wsl("2", 1991, "Smith", "Jane", district=7),
        ],
    )
    assert report.identities == ()
    (refusal,) = report.refused
    assert refusal.reason == REFUSED_JOIN_AMBIGUOUS


def test_crossing_name_with_no_candidate_and_no_adjudication_is_refused() -> None:
    """A future edition's new crossing name must surface, not silently mint a Person that
    may duplicate a WSL identity (the fork §2 exists to prevent)."""
    report = resolve_identities(
        [
            _rec("New Person", 1989, district=9),
            _rec("New Person", 1991, district=9),
        ],
        seatings=(),
    )
    assert report.identities == ()
    (refusal,) = report.refused
    assert refusal.reason == REFUSED_JOIN_UNRESOLVED


# --- the partition oracle ----------------------------------------------------


def test_every_pre_floor_record_lands_in_exactly_one_bucket() -> None:
    """Oracle item 1: identities plus refusals partition the pre-1991 input exactly —
    zero silent drops, zero double-counting."""
    records = [
        _rec("A. B. Carver", 1899),
        _rec("A. B. Carver", 1901),
        _rec("Victor Zednick", 1917, district=43),
        _rec("Victor Zednick", 1943, chamber="senate", district=36),
        _rec("Jane Doe", 1985, district=5),
        _rec("Jane Doe", 1991, district=5),
    ]
    report = resolve_identities(records, seatings=[_wsl("42", 1991, "Doe", "Jane")])
    pre = [r for r in records if r.year < 1991]
    placed = [r for i in report.identities for r in i.records] + [
        r for ref in report.refused for r in ref.records
    ]
    assert sorted((r.name, r.year) for r in placed) == sorted((r.name, r.year) for r in pre)


def test_shipped_adjudication_tables_have_the_measured_shape() -> None:
    """The versioned data the spec requires: the three §3 join adjudications, the
    elmerejohnston split, and the measured alias set."""
    assert JOIN_ADJUDICATIONS["billdayjr"] == "103"
    assert JOIN_ADJUDICATIONS["bobbasich"] == "23"
    assert JOIN_ADJUDICATIONS["shirleygalloway"] is None
    assert IDENTITY_SPLITS["elmerejohnston"] == 1947
    assert IDENTITY_ALIASES["phillipmcdonough"] == "philipmcdonough"
    assert CORROBORATION_FLOOR == 2
    assert WIDE_GAP_YEARS == 20


def test_summary_counts_dispositions_and_reasons() -> None:
    report = resolve_identities(
        [
            _rec("A. B. Carver", 1899),
            _rec("Victor Zednick", 1917, district=43),
            _rec("Victor Zednick", 1943, chamber="senate", district=36),
        ],
        seatings=(),
    )
    counts = report.summary()
    assert counts[IDENTITY_MINTED] == 1
    assert counts[f"refused:{REFUSED_WIDE_GAP}"] == 1


# ---------------------------------------------------------------------------
# #259 — the floor is a *listing-year* floor, but a Senate term crosses it


def test_senate_term_starting_two_years_below_the_floor_joins_the_wsl_member() -> None:
    """Patty Murray's shape, measured: elected 1988, seated 1989, served through 1992.

    The roster indexes rows by **term-start year**, so her only listing is 1989 — yet a
    four-year Senate term reaches the 1991-92 biennium, where WSL holds her. Keying the
    join on "has a 1991+ listing" misses her and mints a duplicate of a Person we already
    hold: the §2 fork. 14 real members resolved this way before the fix.
    """
    report = resolve_identities(
        [_rec("Patty Murray", 1989, chamber="senate", district=1)],
        seatings=[_wsl("299", 1991, "Murray", "Patty", district=1, chamber="senate")],
    )

    (identity,) = report.identities
    assert identity.disposition == IDENTITY_WSL
    assert identity.wsl_member_id == "299"
    assert identity.key is None
    assert [r.year for r in identity.records] == [1989]


def test_a_house_term_starting_two_years_below_the_floor_still_mints() -> None:
    """The rule is the term length, not the year. A House term starting 1989 ends in 1990
    — it never reaches the floor, so a same-surname senator seated in 1991 is a different
    person and joining them would be the merge #228 forbids."""
    report = resolve_identities(
        [_rec("Patty Murray", 1989, chamber="house", district=1)],
        seatings=[_wsl("299", 1991, "Murray", "Patty", district=1, chamber="house")],
    )

    (identity,) = report.identities
    assert identity.disposition == IDENTITY_MINTED
    assert identity.key == "pattymurray:1989"


def test_a_senate_term_ending_before_the_floor_still_mints() -> None:
    """A 1987 Senate term covers 1987-1990 and stops short of the floor — a genuine
    retirement, not a crosser. Only terms whose span reaches the floor probe WSL."""
    report = resolve_identities(
        [_rec("Sam Early", 1987, chamber="senate", district=1)],
        seatings=[_wsl("500", 1991, "Early", "Sam", district=1, chamber="senate")],
    )

    (identity,) = report.identities
    assert identity.disposition == IDENTITY_MINTED
    assert identity.key == "samearly:1987"


def test_a_boundary_senator_with_no_wsl_seat_mints() -> None:
    """A 1989 senator whose seat has nobody of that surname in 1991 genuinely departed
    (resignation, death, appointment out) — mint, don't refuse."""
    report = resolve_identities(
        [_rec("Gone Bysummer", 1989, chamber="senate", district=7)],
        seatings=[_wsl("501", 1991, "Other", "Person", district=7, chamber="senate")],
    )

    (identity,) = report.identities
    assert identity.disposition == IDENTITY_MINTED
    assert identity.key == "gonebysummer:1989"


def test_the_boundary_probe_respects_the_given_name_initial_guard() -> None:
    """The #240 guard is not bypassed at the boundary: a surname match whose initials
    share nothing is a different person, and a lone 1989 listing offers no corroborating
    years to overturn it."""
    report = resolve_identities(
        [_rec("Alice Smith", 1989, chamber="senate", district=4)],
        seatings=[_wsl("502", 1991, "Smith", "Robert", district=4, chamber="senate")],
    )

    (identity,) = report.identities
    assert identity.disposition == IDENTITY_MINTED
    assert identity.key == "alicesmith:1989"


def test_an_ambiguous_boundary_probe_refuses_rather_than_mints() -> None:
    """CR #108: "no candidate" means the senator retired — mint. "Two compatible
    candidates" means we do not know which, and minting records a decision we have not
    earned: the §2 fork, silently. The crossing path already refuses on ambiguity; the
    boundary path must agree."""
    report = resolve_identities(
        [_rec("J. Smith", 1989, chamber="senate", district=6)],
        seatings=[
            _wsl("601", 1991, "Smith", "John", district=6, chamber="senate"),
            _wsl("602", 1991, "Smith", "Jane", district=6, chamber="senate"),
        ],
    )

    assert not report.identities
    (refusal,) = report.refused
    assert refusal.reason == REFUSED_JOIN_AMBIGUOUS
    assert refusal.fold == "jsmith"


def test_an_unknown_chamber_at_the_boundary_raises() -> None:
    """CR #107: a silent zero-length term never probes and mints a duplicate. The audit
    oracle already refuses to default an unrecognised chamber (its own CR finding 5); a
    silent fork is a worse outcome than a loud failure, not a better one."""
    with pytest.raises(KeyError):
        resolve_identities(
            [_rec("Odd Chamber", 1989, chamber="tribunal", district=1)],
            seatings=[],
        )


def test_the_initial_guard_ignores_annotation_text_in_the_name() -> None:
    """Measured on the real corpus: LD22 Senate 1991 holds BOTH Kreidlers — Mike, and Lela,
    appointed to cover his military leave. The roster row carries that annotation inside the
    name, and the #240 guard took its initials from the raw string, so "**l**eave" put `l`
    in the set and made **L**ela compatible. Two compatible candidates where the evidence
    names one. The guard must read the same cleaned name the fold does."""
    report = resolve_identities(
        [
            _rec(
                'Myron "Mike" Kreidler (On leave of absence for military duty)',
                1989,
                chamber="senate",
                district=22,
            )
        ],
        seatings=[
            _wsl("232", 1991, "Kreidler", "Lela", district=22, chamber="senate"),
            _wsl("233", 1991, "Kreidler", "Mike", district=22, chamber="senate"),
        ],
    )

    assert not report.refused
    (identity,) = report.identities
    assert identity.disposition == IDENTITY_WSL
    assert identity.wsl_member_id == "233"


# ---------------------------------------------------------------------------
# usa-wa#226 — the resolved identities ARE the pre-1991 seating index


def test_identity_seatings_key_each_record_to_its_resolved_identity() -> None:
    """The #226 resolver matches a dated boundary against a seating index built only from
    WSL sponsor archives, which floor at 1991 — so every pre-1991 boundary resolves
    ``no_member`` by construction, 363 of them in the live corpus. That is not a name-match
    failure: there was nothing to match against.

    #228 changed the ground. Every pre-floor record now belongs to a resolved identity with
    a real Person behind it, so the identities themselves are the missing index. Deriving it
    here rather than from canonical rows keeps the member ids identical to the ones the mint
    used — a re-derivation could drift, and a drifted member id writes an operator event
    against a Person that does not exist.
    """
    rows = [
        _rec("Belle (Mrs. Frank) Reeves", 1923, chamber="house", district=13),
        _rec("Belle (Mrs. Frank) Reeves", 1925, chamber="house", district=13),
    ]
    report = resolve_identities(rows, seatings=())
    (identity,) = report.identities

    seatings = identity_seatings(report)

    assert {(s.member_id, s.year, s.chamber, s.district) for s in seatings} == {
        (identity.key, 1923, "house", 13),
        (identity.key, 1925, "house", 13),
    }
    # Surname and given name mirror WSL's LastName/FirstName so the resolver's #240
    # given-name-initial guard reads the same shape from either index.
    assert {s.surname for s in seatings} == {"reeves"}
    assert {s.given_name for s in seatings} == {"belle"}


def test_a_joined_identity_is_keyed_by_its_WSL_member_id() -> None:
    """A crosser already has a WSL Person; the roster asserts no new key for it. Keying its
    pre-1991 records by the roster fold would mint an operator event against an identity
    that was never minted."""
    rows = [
        _rec("Jay Inslee", 1989, chamber="house", district=13),
        _rec("Jay Inslee", 1991, chamber="house", district=13),
    ]
    seatings = (
        Seating(
            member_id="M-1",
            chamber="house",
            district=13,
            year=1991,
            surname="Inslee",
            given_name="Jay",
        ),
    )
    report = resolve_identities(rows, seatings=seatings)

    derived = identity_seatings(report)

    assert {s.member_id for s in derived} == {"M-1"}
    # Only the pre-floor record is carried: 1991+ rows belong to the WSL index already.
    assert {s.year for s in derived} == {1989}


def test_a_refused_identity_contributes_no_seatings() -> None:
    """#228's rule holds: an unresolved identity is surfaced, never guessed. A seating for
    it would resolve a boundary onto a Person we declined to mint."""
    rows = [_rec("Ambiguous Person", 1923, chamber="house", district=13)]
    report = IdentityReport(
        identities=(),
        refused=(
            RefusedIdentity(
                reason="whatever", fold="ambiguousperson", records=tuple(rows), detail="-"
            ),
        ),
    )

    assert identity_seatings(report) == []
