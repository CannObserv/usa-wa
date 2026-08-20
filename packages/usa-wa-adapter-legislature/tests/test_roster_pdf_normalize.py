"""The roster-PDF parser (#225) — pure, over extracted word geometry.

The fixture is the source's own District 2 pages (PDF pages 23-25,
revision 2025-06-05), which between them exercise every parser hazard the spec names:
a two-column body, a year gutter that is a *separate* text block from the name block,
Senate four-year terms (skipped years), wrapped annotations, a minor-party token, and a
mid-biennium succession chain whose ordering encodes seat lineage.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from usa_wa_adapter_legislature.roster_pdf.cohort import extract_pages
from usa_wa_adapter_legislature.roster_pdf.normalize import (
    PARTY_TOKENS,
    PageWords,
    _has_undeclared_party_tail,
    _split_annotation,
    parse_district_pages,
    parse_district_pages_reporting,
)
from usa_wa_common.parties import PARTY_UNRECOGNIZED, resolve_party_token

FIXTURE = Path(__file__).parent / "fixtures" / "roster_pdf_d2.pdf"


@pytest.fixture(scope="module")
def d2_pages() -> list[PageWords]:
    """Real word geometry from the source's District 2 pages (PDF pp.23-25, revision
    2025-06-05), font-subset for size. One fixture, extracted the way production does."""
    return extract_pages(FIXTURE.read_bytes())


@pytest.fixture(scope="module")
def records(d2_pages: list[PageWords]):
    return parse_district_pages(d2_pages)


def test_parses_both_chambers_of_district_2(records) -> None:
    assert {r.district for r in records} == {2}
    assert {r.chamber for r in records} == {"senate", "house"}


def test_year_gutter_is_joined_to_the_name_block(records) -> None:
    """The regression that matters: the year is a separate text block from the name, so a
    line-oriented parse recovers ~5% of rows. Every record must carry a year."""
    assert records, "no records parsed"
    assert all(r.year is not None for r in records)
    assert all(1889 <= r.year <= 2025 for r in records)


def test_recovers_both_columns(records) -> None:
    """A single-column parse would drop the right column entirely, roughly halving the count
    and truncating the year range at mid-century."""
    house = [r for r in records if r.chamber == "house"]
    assert max(r.year for r in house) >= 2025
    assert min(r.year for r in house) <= 1901


def test_ld2_house_position_1_lineage_in_order(records) -> None:
    """Row order within a district-year is seat-lineage order: the Position 1 lineage
    (Bush -> McCune -> Alexander/Hunt -> Barkis) sorts before the Position 2 lineage
    (Campbell -> Wilcox -> Marshall). Verified against SOS-derived truth for these years."""
    by_year = {}
    for r in records:
        if r.chamber == "house":
            by_year.setdefault(r.year, []).append(r)
    for year in (2003, 2005, 2011, 2025):
        rows = sorted(by_year[year], key=lambda r: r.order)
        assert len(rows) >= 2, f"{year}: {rows}"
    assert sorted(by_year[2003], key=lambda r: r.order)[0].name == "Roger Bush"
    assert sorted(by_year[2003], key=lambda r: r.order)[-1].name == "Tom Campbell"
    # 2005 proves the order is lineage, not alphabetical: McCune sorts before Campbell.
    assert sorted(by_year[2005], key=lambda r: r.order)[0].name == "Jim McCune"
    assert sorted(by_year[2005], key=lambda r: r.order)[-1].name == "Tom Campbell"
    assert sorted(by_year[2025], key=lambda r: r.order)[0].name == "Andrew Barkis"
    assert sorted(by_year[2025], key=lambda r: r.order)[-1].name == "Matt Marshall"


def test_succession_annotations_carry_their_dates(records) -> None:
    """The accuracy payload: 461 appointments / 325 resignations / 136 deaths are dated
    inline. LD2 2013-2015 is the worked case from the spec."""
    hunt = [r for r in records if r.name == "Graham Hunt"]
    assert hunt, "Graham Hunt not parsed"
    appointed = next(r for r in hunt if r.year == 2013)
    assert appointed.annotation is not None
    assert "January 17, 2014" in appointed.annotation
    alexander = next(r for r in records if r.name == "Gary C. Alexander" and r.year == 2013)
    assert alexander.annotation is not None
    assert "Dec. 31, 2013" in alexander.annotation


def test_wrapped_annotation_never_becomes_a_member_row(records) -> None:
    """An annotation wraps across lines and a continuation can itself end in dots + a party
    letter, so it parses as a spurious member. No record's name may look like prose."""
    for r in records:
        assert not r.name.endswith(")"), r
        assert not r.name[0].islower(), r
        assert "unexpired" not in r.name, r
        assert not r.name.startswith("to serve"), r


def test_minor_party_token_survives_verbatim(records) -> None:
    """Party tokens are carried through as the source writes them — canonicalisation is a
    separate concern (#227), and folding an unknown token to None here is the silent-drop bug."""
    field = next(r for r in records if r.name == "Willard B. Field")
    assert field.party_token == "P.P."
    assert {r.party_token for r in records} >= {"R", "D", "P.P."}


def test_senate_terms_skip_years(records) -> None:
    """Senate seats run four-year terms, so its year sequence skips sessions the House's covers.

    LD2's House rows begin at 1899 (the district gained House representation later), so the
    contrast is drawn on 1901 -- a session the House lists and the Senate, mid-term, does not.
    """
    senate_years = sorted({r.year for r in records if r.chamber == "senate"})
    house_years = sorted({r.year for r in records if r.chamber == "house"})
    assert 1901 in house_years
    assert 1901 not in senate_years
    assert len(house_years) > len(senate_years)


def test_parser_is_pure(d2_pages: list[PageWords]) -> None:
    """Two runs over the same input give equal output — the builder re-drives on every run."""
    assert parse_district_pages(d2_pages) == parse_district_pages(d2_pages)


class TestChamberIsAYDivider:
    """A district's Senate and House blocks can share one page, so the chamber is a full-width
    divider at a y-position — not a page-level property read from the running header.

    Verified on page 104 of the 2025-06-05 edition: the running header says "Senate", the
    ``SENATE`` banner sits at y=109, ``HOUSE OF REPRESENTATIVES`` at y=499, and W. H. Kingery's
    row at y=631 is plainly below the divider. Reading the chamber once per page put him in the
    Senate. 35 of the 166 district pages carry both banners, so this is systematic.

    Brazier's *History of the Washington Legislature 1854-1963* is the independent check: the
    1913 Socialist sat in the **House**; the 1913 Senate's lone third-party member was an
    Independent. Attributing the Socialist to the Senate both invents an assignment and
    discards a real one, since ``Ind`` folds to no party (#233).
    """

    def test_a_row_below_the_house_banner_is_house(self, full_pages) -> None:
        from usa_wa_adapter_legislature.roster_pdf.normalize import parse_district_pages

        records = parse_district_pages(full_pages)
        kingery = [r for r in records if "Kingery" in r.name and r.year == 1913]
        assert kingery, "Kingery not parsed"
        assert kingery[0].chamber == "house"

    def test_a_row_above_the_divider_stays_senate(self, full_pages) -> None:
        """The other half of the rule: the fix must not sweep the whole page into the House.

        District 31's Senate block sits above the y=499 divider on this page, and Halteman's
        1895 row is below it — so the two together pin both directions of the divider.
        """
        from usa_wa_adapter_legislature.roster_pdf.normalize import parse_district_pages

        records = parse_district_pages(full_pages)
        senate_31 = [r for r in records if r.district == 31 and r.chamber == "senate"]
        assert senate_31, "district 31 lost its Senate block entirely"
        halteman = [r for r in records if "Halteman" in r.name]
        assert halteman, "Halteman not parsed"
        assert halteman[0].chamber == "house"
        # A row from the Senate block above the divider keeps its chamber.
        assert min(r.year for r in senate_31) < 1913


class TestParentheticalNamesSurviveTheSplit:
    """A parenthetical is only an annotation when its *content* is prose.

    The source writes marital and nickname forms inline — ``Margaret (Mrs. Joseph E.) Hurley``,
    ``Judith (Judy) Warnick``. Splitting on the bare parenthesis strands the surname in the
    annotation and leaves the name a bare given name, which destroys identity for those members
    (39 records / 7 members in the 2025 edition) and would mint Persons called "Margaret".
    """

    @pytest.mark.parametrize(
        ("raw", "name", "annotation"),
        [
            # Marital and nickname forms — verbatim from the 2025 edition. The surname sits
            # AFTER the parenthetical, so splitting on the bracket loses it entirely.
            ("Margaret (Mrs. Joseph E.) Hurley", "Margaret (Mrs. Joseph E.) Hurley", None),
            ("Judith (Judy) Warnick", "Judith (Judy) Warnick", None),
            ("Agnes (Mrs. Thomas E.) Kehoe", "Agnes (Mrs. Thomas E.) Kehoe", None),
            ("Belle (Mrs. Frank) Reeves", "Belle (Mrs. Frank) Reeves", None),
            # Prose parentheticals still split out.
            ("Wayne Ehlers (Speaker)", "Wayne Ehlers", "Speaker"),
            (
                "Gary C. Alexander (Resigned Dec. 31, 2013)",
                "Gary C. Alexander",
                "Resigned Dec. 31, 2013",
            ),
            # Both at once: the nickname stays, the prose splits.
            (
                "Judith (Judy) Warnick (Resigned May 1, 2013)",
                "Judith (Judy) Warnick",
                "Resigned May 1, 2013",
            ),
        ],
    )
    def test_split_keeps_name_parentheticals_and_splits_prose(
        self, raw: str, name: str, annotation: str | None
    ) -> None:
        assert _split_annotation(raw) == (name, annotation)

    def test_a_nickname_stays_in_the_name(self, records) -> None:
        assert not any(r.name == "Judith" for r in records)

    def test_no_record_name_is_a_bare_given_name(self, records) -> None:
        """Every surviving name must carry more than one token once nicknames are folded in."""
        for r in records:
            assert " " in r.name, r

    def test_a_prose_parenthetical_is_still_an_annotation(self, records) -> None:
        speaker = [r for r in records if r.annotation and "Speaker" in r.annotation]
        assert speaker, "prose parentheticals must still split out"
        assert all("Speaker" not in r.name for r in speaker)


# --- the undeclared-token drop detector (#227 CR #50) ------------------------


def _retoken(pages: list[PageWords], old: str, new: str) -> list[PageWords]:
    """The real fixture with the first party token ``old`` rewritten to ``new``.

    Mutating real word geometry rather than synthesising it: the parser's band and column
    rules are geometric, so a hand-built page exercises the regexes without exercising the
    path that actually reaches them.
    """
    done = False
    out: list[PageWords] = []
    for page in pages:
        words = []
        for word in page.words:
            if not done and word.text == old:
                words.append(replace(word, text=new))
                done = True
            else:
                words.append(word)
        out.append(replace(page, words=tuple(words)))
    assert done, f"fixture has no {old!r} token to rewrite"
    return out


def test_undeclared_party_token_is_reported_not_dropped(d2_pages) -> None:
    """CR #50 — the defect this closes. ``_ROW_PARTY`` is a **closed alternation** over
    ``PARTY_TOKENS``, so a row whose party abbreviation the source newly introduces fails that
    match, fails ``_PROSE``, and was dropped by the silent-furniture branch. It never reached
    ``unparsed``, so ``usa_wa_common.parties``' ``unknown_token`` guardrail could not fire from
    this path at all, and #227's party oracle could report a clean run over only the survivors.
    """
    baseline = parse_district_pages_reporting(d2_pages)
    mutated = parse_district_pages_reporting(_retoken(d2_pages, "D", "Whig"))

    assert len(mutated.records) == len(baseline.records) - 1  # the row did not parse
    assert len(mutated.unparsed) == len(baseline.unparsed) + 1  # and was reported
    assert any("Whig" in line for line in mutated.unparsed), mutated.unparsed


def test_declared_party_token_leaves_the_report_untouched(d2_pages) -> None:
    """The detector must not perturb the happy path: swapping one declared token for another
    keeps every row parsing and adds nothing to ``unparsed``."""
    baseline = parse_district_pages_reporting(d2_pages)
    mutated = parse_district_pages_reporting(_retoken(d2_pages, "D", "Pop."))

    assert len(mutated.records) == len(baseline.records)
    assert mutated.unparsed == baseline.unparsed


def test_repeated_dot_leader_resolves_to_its_real_trailing_token() -> None:
    """Regression on the detector's own first draft. A greedy tail match read
    ``"…) ....D ....... D"`` as the two-word tail ``"D ....... D"``, which flagged two real
    rows in the live edition as undeclared. The tail admits one interior space (the source
    already has ``Silver Rep.``) but no dot runs, so it resolves to the final ``D``."""
    assert not _has_undeclared_party_tail(
        "R. L. Nye (Port Orchard Housing Authority) ........D ........... D"
    )
    assert not _has_undeclared_party_tail("Amos P. Whitfield ......................... Silver Rep.")
    assert _has_undeclared_party_tail("Amos P. Whitfield ......................... Whig")
    assert _has_undeclared_party_tail("Amos P. Whitfield ......................... Free Soil")


def test_two_dot_leader_is_within_reach_and_leaderless_is_not() -> None:
    """CR #59: the leader is *optional* in this source, so a detector keyed on it cannot see
    every member row. Two dots was chosen over three because it measured identically clean
    (0 false positives on the live edition) while shrinking the residual — the source really
    does typeset ``"… (Resigned, Appntd to the Senate) ..D"``.

    The second assertion pins the **known** blind spot rather than papering over it: 13 of
    4,601 lines ending in a declared token carry no leader at all, and a genuinely new token
    landing on one of those is still dropped. Closing that needs a signal other than the leader.
    """
    assert _has_undeclared_party_tail("Victoria Hunt (Resigned, Appntd to the Senate) ..Whig")
    assert not _has_undeclared_party_tail("Appointed U.S. Marshal, Western District of WA.) Whig")


def test_every_declared_party_token_is_classified() -> None:
    """CR #49: the parser's ``PARTY_TOKENS`` and the vocabulary's resolver are two halves of
    one contract, and they had drifted — ``Soc.`` and the dotless ``Ind`` fell to
    ``unrecognized``/``unknown_token``, the "nobody has adjudicated this" bucket, despite the
    parser declaring both. Neither appears in the current edition, so the #227 oracle passed
    anyway; a different revision would have tallied them as unknown.

    This is the test that keeps the two lists honest. It belongs here, not in
    ``usa-wa-common``: Layer 2b may not import an adapter, and the adapter is the side that
    owns the source vocabulary."""
    unclassified = {
        token
        for token in PARTY_TOKENS
        if resolve_party_token(token, year=1915).disposition == PARTY_UNRECOGNIZED
    }
    assert not unclassified, (
        f"parser declares tokens the vocabulary cannot classify: {unclassified}"
    )


# ---------------------------------------------------------------------------
# #252 — year state must not thread across chamber blocks at column boundaries


FIXTURE_D26 = Path(__file__).parent / "fixtures" / "roster_pdf_d26_interleaved.pdf"


@pytest.fixture(scope="module")
def d26_records():
    """PDF page 89 (revision 2025-06-05): District 26, whose Senate block spills into the
    right column's top while the House block starts at the left column's bottom. The #252
    page — a year-less successor row at the top of the right column continues a year group
    from the *other column's* Senate block, across the intervening House rows."""
    return parse_district_pages_reporting(extract_pages(FIXTURE_D26.read_bytes())).records


def test_column_boundary_successor_resumes_its_own_chamber_year(d26_records) -> None:
    """Beck's year-less appointment row continues Gardner's 1971 Senate group (#252).

    The left column exits in the House block at 1899; without per-chamber year state the
    right column's first row inherits that year and emits ``1899 senate ord3`` — a
    seventy-five-year mis-attribution that corrupts #228 identity keys and puts two
    simultaneous holders on LD26's Senate seat in 1899.
    """
    beck = [r for r in d26_records if "Beck" in r.name]
    assert [(r.year, r.chamber, r.order) for r in beck] == [
        (1971, "senate", 2),  # appointed Feb. 11, 1974 — successor row in Gardner's group
        (1975, "senate", 1),  # his own elected term
    ]
    assert "Appointed Feb. 11, 1974" in (beck[0].annotation or "")


def test_column_boundary_leaves_neighbouring_groups_intact(d26_records) -> None:
    """The fix must not disturb the rows around the boundary: the 1899 groups of *both*
    chambers keep their own occupants, and the House block resumes its sequence when the
    right column crosses back below the divider."""
    senate_1899 = [r for r in d26_records if r.chamber == "senate" and r.year == 1899]
    assert [(r.name, r.order) for r in senate_1899] == [("Harold Preston", 1)]
    house_1899 = [r for r in d26_records if r.chamber == "house" and r.year == 1899]
    assert [(r.name, r.order) for r in house_1899] == [("E. P. Kingsbury", 1), ("George McCoy", 2)]
    house_1901 = [r for r in d26_records if r.chamber == "house" and r.year == 1901]
    assert [(r.name, r.order) for r in house_1901] == [("George McCoy", 1), ("H. M. Ingraham", 2)]


# ---------------------------------------------------------------------------
# #252 — a row whose leader+party wrap onto the next line must not vanish


FIXTURE_D28 = Path(__file__).parent / "fixtures" / "roster_pdf_d28_wrapped.pdf"


@pytest.fixture(scope="module")
def d28_records():
    """PDF page 95 (revision 2025-06-05): District 28. Charles E. Newschwander's 1969 Senate
    row wraps — the name line carries no dotted leader, the ``...... R`` fragment and the
    ``(Elected Nov 5, 1968 …)`` annotation follow on later lines — so the name line reads as
    furniture, the row vanishes, and its debris glues onto the last emitted record (E. L.
    Minard's 1899 House row, a different member in a different chamber and era)."""
    return parse_district_pages_reporting(extract_pages(FIXTURE_D28.read_bytes())).records


def test_wrapped_row_with_detached_leader_is_emitted(d28_records) -> None:
    """The 1969 Senate listing exists: Newschwander, party from the wrapped fragment (#252)."""
    nw = [r for r in d28_records if "Newschwander" in r.name]
    assert [(r.year, r.chamber, r.order, r.party_token) for r in nw] == [
        (1969, "senate", 1, "R"),
        (1973, "senate", 1, "R"),
        (1977, "senate", 1, "R"),
    ]
    assert "Elected Nov 5, 1968" in (nw[0].annotation or "")


def test_wrapped_row_debris_does_not_pollute_the_previous_record(d28_records) -> None:
    """Minard's 1899 House row keeps its own (absent) annotation — the 1968 election
    annotation belongs to Newschwander's Senate row, not to a member seated 69 years
    earlier (#252)."""
    minard = [r for r in d28_records if "Minard" in r.name]
    assert [(r.year, r.chamber, r.annotation) for r in minard] == [(1899, "house", None)]


# ---------------------------------------------------------------------------
# #252 — the footer band must not swallow the last member rows of a full page


FIXTURE_D10 = Path(__file__).parent / "fixtures" / "roster_pdf_d10_footer.pdf"


@pytest.fixture(scope="module")
def d10_records():
    """PDF page 45 (revision 2025-06-05): District 10, a full page whose House block's last
    line of member rows sits at 91.7% of page height — above the printed footer (96.5%) but
    below the 0.909 footer band, so George Windust's 1897 row and W. O. Long's 1905 row
    were silently cut. Fourteen of the fifteen residual house listing gaps are this shape."""
    return parse_district_pages_reporting(extract_pages(FIXTURE_D10.read_bytes())).records


def test_footer_band_keeps_the_last_member_rows(d10_records) -> None:
    """The bottom line of the page's House block parses in both columns (#252)."""
    house = {(r.year, r.name) for r in d10_records if r.chamber == "house"}
    assert (1897, "George Windust") in house
    assert (1905, "W. O. Long") in house


def test_footer_band_still_excludes_the_printed_footer(d10_records) -> None:
    """No page-footer furniture leaks into records or annotations (#252): the page number,
    the edition line, and the bold-years legend stay outside the member table."""
    for r in d10_records:
        for fragment in ("Members of the Legislature, 2025", "Bold years", "- 39 -"):
            assert fragment not in r.name
            assert fragment not in (r.annotation or "")
