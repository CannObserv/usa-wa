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

#: ``PARTY_TOKENS`` folded for the drop-detector below.
_PARTY_TOKENS_FOLDED = {t.lower() for t in PARTY_TOKENS}

#: A member row's *shape* — a dot leader, then a short trailing token — used only to decide
#: whether a line ``_ROW_PARTY`` rejected still looks like a member row (#227 CR #50).
#:
#: ``_ROW_PARTY`` is a closed alternation over ``PARTY_TOKENS``, so a row whose party
#: abbreviation the source has newly introduced fails that match, fails ``_PROSE``, and was
#: **dropped silently as furniture**. That is the never-silently-drop rule broken one layer
#: above the vocabulary that was written to enforce it: ``resolve_party_token``'s
#: ``unknown_token`` branch could never fire from this path, so the #227 party oracle could
#: report a clean run over exactly the records that survived the parse. Detecting the shape
#: here routes such a row to ``unparsed`` instead, where the report-don't-drop contract sees it.
#:
#: One optional interior space, so a two-word abbreviation (the source already has
#: ``Silver Rep.``) qualifies; no dot runs inside the tail, so a row that merely repeats its
#: leader — ``"… ) ....D ....... D"`` — resolves to its real trailing token rather than
#: swallowing the gap. Measured against the archived edition: **0 lines**, so this adds no
#: noise and fires only on a genuinely new token.
#:
#: **The leader threshold is two dots, and the coverage is not total** (#227 CR #59). The
#: leader is *optional* in this source — a long annotation can consume it entirely — so a
#: detector keyed on it cannot see every member row. Measured: **13 of 4,601** lines ending in
#: a declared token (0.3%) carry no leader of two or more dots, e.g.
#: ``"… (Resigned, Appntd to the Senate) ..D"`` (caught at two, missed at three) and
#: ``"… Appointed U.S. Marshal, Western District of WA.) D"`` (missed at either). Two dots was
#: chosen over three because it measured identically clean — 0 false positives — while
#: shrinking that residual. A genuinely new token landing on one of the remaining leaderless
#: rows is still dropped; closing that needs a signal other than the leader.
_LEADER_TAIL = re.compile(r"\.{2,}\s*(?P<tail>[A-Za-z][A-Za-z.]*(?: [A-Za-z][A-Za-z.]*)?)\s*$")


def _has_undeclared_party_tail(body: str) -> bool:
    """Whether ``body`` has member-row shape but ends in a token ``PARTY_TOKENS`` lacks (#227)."""
    match = _LEADER_TAIL.search(body)
    if match is None:
        return False
    return " ".join(match.group("tail").split()).lower() not in _PARTY_TOKENS_FOLDED


#: An optional leading session year: the year sits in a gutter but is part of the same physical
#: line, so it is the row's first token rather than a separate block.
_LEADING_YEAR = re.compile(r"^(?P<year>1[89]\d{2}|20\d{2})\s+(?P<rest>.*)$")

#: ``DISTRICT NO. 12`` (banner) or ``District No. 12`` (running header).
_DISTRICT = re.compile(r"DISTRICT NO\.\s*(\d+)", re.IGNORECASE)

#: The centred chamber banners that open a district's Senate and House blocks. A district's two
#: blocks routinely share one page (35 of 166 district pages in the 2025 edition), so the chamber
#: is a **full-width divider at a y-position**, not a page-level property. Reading it once from
#: the running header put every House row on such a page into the Senate — which is how the 1913
#: Socialist ended up as a senator (#233).
_CHAMBER_BANNER = re.compile(r"^\s*(SENATE|HOUSE OF REPRESENTATIVES)\s*$", re.IGNORECASE)

#: Prose that must never be mistaken for a member name once a wrapped annotation is joined.
_PROSE = re.compile(
    r"unexpired|^to serve|^Sworn in|^Elected \w+\.? \d|^Appointed|^Served |^Named |"
    r"^Changed party|^\(|^Resigned|^Deceased|^\d",
    re.IGNORECASE,
)

#: What makes a parenthetical an *annotation* rather than part of the name — tested against the
#: parenthetical's **content**, never against the bracket itself.
#:
#: The source writes marital and nickname forms inline (``Margaret (Mrs. Joseph E.) Hurley``,
#: ``Judith (Judy) Warnick``). Treating every parenthetical as prose stranded the surname in the
#: annotation and left the name a bare given name — 39 records across 7 members in the 2025
#: edition, which would mint Persons called "Margaret" and make them unmatchable to any wire.
_ANNOTATION_CUE = re.compile(
    r"^(?:Appointed|Resigned|Deceased|Died|Elected|Sworn|Served|Named|Changed|Redistricted|"
    r"Holdover|Speaker|President|Election|Vacan|Position|Contested|Removed|Expelled|Term|"
    r"Died|To serve|Unseated|Seated|Succeeded|Replaced|Withdrew|Died)\b|unexpired",
    re.IGNORECASE,
)

#: Header/footer bands as a **fraction of page height**, not absolute points. The column split
#: is already width-relative, so absolute bands were the one geometry constant that would break
#: silently on a future edition typeset at a different page size — dropping rows rather than
#: failing (CR finding 7).
_HEADER_FRACTION = 0.101
_FOOTER_FRACTION = 0.909

#: The page height these fractions were derived from (US Letter, 792pt), used when a caller
#: supplies no height.
_DEFAULT_PAGE_HEIGHT = 792.0

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
    """One page's extracted words plus its size.

    Both band and column geometry are expressed relative to these, so an edition typeset at a
    different page size degrades visibly rather than silently dropping rows.
    """

    page_number: int
    width: float
    height: float = _DEFAULT_PAGE_HEIGHT
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

    A parenthetical is an annotation only when its **own content** reads as prose
    (:data:`_ANNOTATION_CUE`). A marital or nickname form — ``Margaret (Mrs. Joseph E.) Hurley``,
    ``Judith (Judy) Warnick`` — stays part of the name, surname included; splitting on the
    bracket alone left those members as bare given names.

    The cue is tested against the **balanced** parenthetical, not the rest of the string: a
    member carrying both forms (``Gladys (Mrs. Douglas G.) Kirk (Appointed …)``) would otherwise
    match the later annotation's cue while splitting at the earlier nickname.
    """
    for i, ch in enumerate(raw):
        if ch != "(":
            continue
        inner = _balanced(raw, i)
        if _ANNOTATION_CUE.search(inner.lstrip()):
            return raw[:i].strip(), raw[i + 1 :].strip().rstrip(")").strip()
    return raw.strip(), None


def _balanced(raw: str, start: int) -> str:
    """The content of the parenthetical opening at ``start``, to its match or the string end.

    Unclosed is normal here: the source contains genuinely unbalanced parentheses, and a
    wrapped annotation may be truncated at a page break.
    """
    depth = 0
    for j in range(start, len(raw)):
        if raw[j] == "(":
            depth += 1
        elif raw[j] == ")":
            depth -= 1
            if depth == 0:
                return raw[start + 1 : j]
    return raw[start + 1 :]


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

        header_band = page.height * _HEADER_FRACTION
        footer_band = page.height * _FOOTER_FRACTION
        head = " ".join(_text(line) for top, line in lines if top < header_band)
        page_chamber = None
        if re.search(r"House of Representatives", head, re.IGNORECASE):
            page_chamber = "house"
        elif re.search(r"\bSenate\b", head, re.IGNORECASE):
            page_chamber = "senate"
        if page_chamber is not None and page_chamber != chamber:
            chamber = page_chamber
            year = None  # a new chamber restarts the year sequence at the district's floor
        # Banner dividers below the running header: everything under one belongs to its chamber.
        # ``SENATE`` -> "senate", ``HOUSE OF REPRESENTATIVES`` -> "house".
        dividers = [
            (top, match.group(1).lower().split()[0])
            for top, line in lines
            if top >= header_band and (match := _CHAMBER_BANNER.match(_text(line)))
        ]
        if chamber is None and not dividers:
            continue

        mid = page.width / 2
        for lo, hi in ((0.0, mid), (mid, float("inf"))):
            column = [
                (top, [w for w in line if lo <= w.x0 < hi])
                for top, line in lines
                if header_band <= top < footer_band
            ]
            column = [(top, line) for top, line in column if line]
            # Year state threads through columns and pages: the right column continues the left
            # column's year sequence, and a year group can span a page break. Resetting per
            # column strands those rows with no year (Jesse Wineberry, LD43).
            year, order = _parse_column(
                column,
                district,
                chamber,
                page.page_number,
                unparsed,
                year,
                order,
                records,
                dividers,
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
    dividers: list[tuple[float, str]],
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
    # None until a buffer opens. 0.0 would silently mean "top of page", which resolves to the
    # first divider — a plausible-looking answer to a question that was never asked.
    buffer_top: float | None = None
    row_chamber = chamber

    def chamber_at(top: float) -> str | None:
        """The chamber whose banner most recently precedes ``top``; the page's otherwise."""
        applicable = [name for divider_top, name in dividers if divider_top <= top]
        return applicable[-1] if applicable else chamber

    for line_top, line in column:
        text = _text(line)
        if not text:
            continue
        if not buffer:
            buffer_top = line_top
        buffer = f"{buffer} {text}".strip() if buffer else text
        at = chamber_at(buffer_top) if buffer_top is not None else row_chamber
        if at is not None and at != row_chamber:
            # Crossing the divider starts a new block, which restarts the year sequence at the
            # district's floor rather than continuing the previous chamber's.
            row_chamber = at
            current_year = None
            order = 0
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
            # or an annotation tail. Attach prose to the row above; drop furniture silently --
            # EXCEPT a line with member-row shape whose trailing token PARTY_TOKENS does not
            # declare, which is a new source abbreviation and must be reported, not dropped
            # (#227 CR #50). Without this the closed alternation makes the party vocabulary's
            # own unknown-token guardrail unreachable.
            if out and _PROSE.match(body):
                out[-1] = _with_annotation(out[-1], body)
            elif _has_undeclared_party_tail(body):
                unparsed.append(buffer)
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

        if row_chamber is None:
            # Discard BEFORE incrementing: ``order`` is the seat-lineage signal #229 infers
            # Positions from, so a consumed-but-unused slot shifts every later row in the group.
            unparsed.append(buffer)
            buffer = ""
            continue
        order += 1
        out.append(
            RosterRecord(
                district=district,
                chamber=row_chamber,
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
