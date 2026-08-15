"""Pure parser for the roster PDF's *by district* section (#225).

**Pure over word geometry, not text.** The source's layout defeats text extraction three ways,
each measured against the real document (233pp, revision 2025-06-05):

1. The **year gutter is a separate text block** from the member-name block. A line-oriented
   parse therefore loses the year association entirely.
2. Each district page is **two columns**. Flattened text interleaves them onto one line.
3. **Annotations wrap**, and a continuation line can itself end in dots + a party letter, so it
   parses as a spurious member row (``"Served in Pos. 1 until December 7, 2012) ..... R"``).

Together (1) and (2) mean a naive line parse recovers **464 of 8,485 records** -- 5%. So rows are
reassembled by y-coordinate within an x-bounded column, which is why the transport hands this
module word bounding boxes rather than text.

Party tokens are carried through **verbatim**. Canonicalisation is a separate concern (#227):
``canonicalize_party`` folds only R/D and returns ``None`` otherwise, so folding here would
silently drop the 167 historical minor-party records. Never key on an exact upstream string --
an unrecognised token is reported, never dropped.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

#: Party tokens the source uses, longest-first so ``Silver Rep.`` wins over a bare ``S``.
#: Verbatim source vocabulary -- not a canonical set (see module docstring).
PARTY_TOKENS: tuple[str, ...] = (
    "Silver Rep.",
    "P.P.",
    "F.L.",
    "Prog.",
    "Pop.",
    "Cit.",
    "Ind.",
    "Soc.",
    "Ind",
    "D",
    "R",
    "S",
)

_PARTY_ALT = "|".join(re.escape(t) for t in PARTY_TOKENS)

#: The party token closing a member row. The dot leader is **optional**: a long annotation can
#: consume the leader entirely (``"David E. McMillan (Redistricted out of district) D"``), so
#: requiring dots drops real rows. The token must still be preceded by a space or a dot, or a
#: trailing ``D`` would match inside a name like ``Ted``.
_ROW_PARTY = re.compile(rf"[\s.]\s*(?P<party>{_PARTY_ALT})$")

#: An optional leading session year: the year sits in a gutter but is part of the same physical
#: line, so it is the row's first token rather than a separate block.
_LEADING_YEAR = re.compile(r"^(?P<year>1[89]\d{2}|20\d{2})\s+(?P<rest>.*)$")

#: ``DISTRICT NO. 12`` (banner) or ``District No. 12`` (running header).
_DISTRICT = re.compile(r"DISTRICT NO\.\s*(\d+)", re.IGNORECASE)

#: Prose that must never be mistaken for a member name once a wrapped annotation is joined.
_PROSE = re.compile(
    r"unexpired|^to serve|^Sworn in|^Elected \w+\.? \d|^Appointed|^Served |^Named |"
    r"^Changed party|^\(|^Resigned|^Deceased|^\d",
    re.IGNORECASE,
)

#: Header/footer band: running heads and the page footer carry no member rows.
_HEADER_BAND = 80.0
_FOOTER_BAND = 720.0

#: Rows within this many points of each other vertically are the same physical line.
_LINE_TOLERANCE = 3.0

#: How many lines a wrapped annotation may span before the row is abandoned as unparsed. The
#: source's longest genuine wrap is three lines; beyond that the accumulation is chasing an
#: unclosed parenthesis and would swallow the rest of the column.
_MAX_CONTINUATION_LINES = 3


@dataclass(frozen=True)
class Word:
    """One extracted word with its bounding box (``top`` grows downward, as pdfplumber reports)."""

    text: str
    x0: float
    x1: float
    top: float


@dataclass(frozen=True)
class PageWords:
    """One page's extracted words plus the page width (the column split is width-relative)."""

    page_number: int
    width: float
    words: Sequence[Word] = field(default_factory=tuple)


@dataclass(frozen=True)
class RosterRecord:
    """One member-year record: who held a seat in this district/chamber in this session year.

    ``order`` is 1-based **within** ``(district, chamber, year)`` and is load-bearing -- it
    encodes seat-lineage order, the signal the House Position inference rests on (#229).
    ``party_token`` is the source's verbatim abbreviation; ``annotation`` is the parenthetical
    prose (succession dates, party changes, redistricting), joined across wrapped lines.
    """

    district: int
    chamber: str
    year: int
    order: int
    name: str
    party_token: str
    annotation: str | None
    page_number: int


@dataclass(frozen=True)
class ParseReport:
    """Records plus what could not be parsed -- report-don't-drop (the never-silently-drop rule)."""

    records: tuple[RosterRecord, ...]
    unparsed: tuple[str, ...]

    @property
    def party_tokens(self) -> set[str]:
        return {r.party_token for r in self.records}


def _lines(words: Sequence[Word]) -> list[tuple[float, list[Word]]]:
    """Group words into physical lines by ``top``, each line's words ordered left to right."""
    grouped: list[tuple[float, list[Word]]] = []
    for word in sorted(words, key=lambda w: (w.top, w.x0)):
        if grouped and abs(word.top - grouped[-1][0]) <= _LINE_TOLERANCE:
            grouped[-1][1].append(word)
        else:
            grouped.append((word.top, [word]))
    for _, line in grouped:
        line.sort(key=lambda w: w.x0)
    return grouped


def _text(line: Sequence[Word]) -> str:
    return " ".join(w.text for w in line).strip()


def _split_annotation(raw: str) -> tuple[str, str | None]:
    """Split ``"Name (Resigned May 11, 1981)"`` into its name and annotation halves.

    Splits on the **first** ``(`` so an interior parenthetical nickname stays with the name
    (``Judith (Judy) Warnick`` must not become name ``Judith`` -- that misparse is what made the
    prototype's ordering check read as a mismatch).
    """
    depth = 0
    for i, ch in enumerate(raw):
        if ch == "(":
            depth += 1
            if depth == 1 and _PROSE.search(raw[i:]):
                return raw[:i].strip(), raw[i:].strip().strip("()").strip()
        elif ch == ")":
            depth -= 1
    return raw.strip(), None


def parse_district_pages(pages: Sequence[PageWords]) -> tuple[RosterRecord, ...]:
    """Parse the *by district* pages into member-year records. Pure and order-preserving."""
    return parse_district_pages_reporting(pages).records


def parse_district_pages_reporting(pages: Sequence[PageWords]) -> ParseReport:
    """:func:`parse_district_pages` plus the unparsed-row tally."""
    records: list[RosterRecord] = []
    unparsed: list[str] = []
    district: int | None = None
    chamber: str | None = None
    year: int | None = None
    order = 0

    for page in pages:
        lines = _lines(page.words)
        page_text = " ".join(_text(line) for _, line in lines)
        found = _DISTRICT.search(page_text)
        if found is None:
            # Outside the *by district* section (front matter, officers, maps, the name index).
            # Reset rather than carrying the last district forward -- the section is bounded by
            # its own banners and running heads, never by a hard-coded page range: the districts
            # run 1-60 historically, so any fixed window silently truncates the tail.
            district = chamber = None
            continue
        if int(found.group(1)) != district:
            district = int(found.group(1))
            chamber = None
            year = None

        head = " ".join(_text(line) for top, line in lines if top < _HEADER_BAND)
        page_chamber = None
        if re.search(r"House of Representatives", head, re.IGNORECASE):
            page_chamber = "house"
        elif re.search(r"\bSenate\b", head, re.IGNORECASE):
            page_chamber = "senate"
        if page_chamber is not None and page_chamber != chamber:
            chamber = page_chamber
            year = None  # a new chamber restarts the year sequence at the district's floor
        if chamber is None:
            continue

        mid = page.width / 2
        for lo, hi in ((0.0, mid), (mid, float("inf"))):
            column = [
                (top, [w for w in line if lo <= w.x0 < hi])
                for top, line in lines
                if _HEADER_BAND <= top < _FOOTER_BAND
            ]
            column = [(top, line) for top, line in column if line]
            # Year state threads through columns and pages: the right column continues the left
            # column's year sequence, and a year group can span a page break. Resetting per
            # column strands those rows with no year (Jesse Wineberry, LD43).
            year, order = _parse_column(
                column, district, chamber, page.page_number, unparsed, year, order, records
            )

    return ParseReport(records=tuple(records), unparsed=tuple(unparsed))


def _parse_column(
    column: list[tuple[float, list[Word]]],
    district: int,
    chamber: str,
    page_number: int,
    unparsed: list[str],
    current_year: int | None,
    order: int,
    out: list[RosterRecord],
) -> tuple[int | None, int]:
    """Parse one column top-to-bottom, joining wrapped annotations before classifying rows.

    Three line shapes occur, and only the first is a record on its own:

    * ``1903 M. E. Stansell ............ R`` -- a row, optionally opening a new year
    * ``1985 R. Ted Bottiger (Resigned November 20, 1987;`` -- a row whose annotation wraps;
      the parenthesis is unbalanced, so the next line completes it
    * ``(Changed party affiliation 1897) ......... Pop.`` -- an annotation belonging to the row
      above. It ends in a party token and would otherwise parse as a spurious member.
    """
    buffer = ""
    carried = 0

    for _, line in column:
        text = _text(line)
        if not text:
            continue
        buffer = f"{buffer} {text}".strip() if buffer else text
        # The party token at the right margin terminates a row -- check it BEFORE the paren
        # balance. The source contains unclosed parentheses (e.g. LD25 2021 Chris Gildon,
        # "...to serve unexpired term" with no closing paren); balancing alone would swallow the
        # remainder of the column into one runaway row.
        if _ROW_PARTY.search(buffer) is None:
            if buffer.count("(") > buffer.count(")") and carried < _MAX_CONTINUATION_LINES:
                carried += 1
                continue  # a wrapped annotation -- keep accumulating
            if carried >= _MAX_CONTINUATION_LINES:
                unparsed.append(buffer)
                buffer = ""
                carried = 0
                continue
        carried = 0

        year_match = _LEADING_YEAR.match(buffer)
        body = year_match.group("rest") if year_match else buffer
        party_match = _ROW_PARTY.search(body)

        if party_match is None:
            # No party token: either non-member furniture (district composition lines, banners)
            # or an annotation tail. Attach prose to the row above; drop furniture silently.
            if out and _PROSE.match(body):
                out[-1] = _with_annotation(out[-1], body)
            buffer = ""
            continue

        raw_name = body[: party_match.start()].rstrip(". ").strip()
        name, annotation = _split_annotation(raw_name)

        if not name or _PROSE.match(name):
            # An annotation line that happens to end in a party token -- belongs to the row above.
            if out:
                out[-1] = _with_annotation(out[-1], f"{raw_name} {party_match.group('party')}")
            else:
                unparsed.append(buffer)
            buffer = ""
            continue

        if year_match:
            current_year = int(year_match.group("year"))
            order = 0
        if current_year is None:
            unparsed.append(buffer)
            buffer = ""
            continue
        if len(name) < 3:
            unparsed.append(buffer)
            buffer = ""
            continue

        order += 1
        out.append(
            RosterRecord(
                district=district,
                chamber=chamber,
                year=current_year,
                order=order,
                name=name,
                party_token=party_match.group("party"),
                annotation=annotation,
                page_number=page_number,
            )
        )
        buffer = ""

    if buffer:
        unparsed.append(buffer)
    return current_year, order


def _with_annotation(record: RosterRecord, extra: str) -> RosterRecord:
    """Append a wrapped annotation fragment onto an already-emitted record."""
    joined = extra.strip() if record.annotation is None else f"{record.annotation} {extra.strip()}"
    return RosterRecord(
        district=record.district,
        chamber=record.chamber,
        year=record.year,
        order=record.order,
        name=record.name,
        party_token=record.party_token,
        annotation=joined.strip(),
        page_number=record.page_number,
    )
