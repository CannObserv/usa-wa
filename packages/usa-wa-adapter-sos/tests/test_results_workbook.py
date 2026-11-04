"""Workbook tests — the ``results.votewa.gov`` *All Results* ``.xlsx`` (#575).

Pins the stdlib read of the workbook's summary sheet, the mapping onto the results-CSV keys the
staging model and the normalizer already consume, and the rows the CSV never carried (the
``Ballots Cast`` / ``Over Votes`` / ``Under Votes`` aggregates, every non-legislative contest).
The real 2026 General workbook, as published 2026-11-04, is the end-to-end fixture.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pytest

from usa_wa_adapter_sos.results import workbook
from usa_wa_adapter_sos.results.normalize import build_house_positions, build_senate_winners
from usa_wa_adapter_sos.results.workbook import (
    WorkbookUnreadable,
    is_workbook,
    legislative_rows,
)

FIXTURE = Path(__file__).parent / "fixtures" / "results_20261103_all_results.xlsx"

_HEADER = ("Office Name", "Contest ID", "Ballot Name", "Choice ID", "Party", "Total")


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _workbook(sheets: dict[str, list[tuple[Any, ...]]]) -> bytes:
    """A minimal EPPlus-shaped workbook: shared strings for text, ``<v>`` numbers, None omitted."""
    strings: list[str] = []
    sheet_xml = []
    for rows in sheets.values():
        cells_by_row = []
        for r, row in enumerate(rows, start=1):
            cells = []
            for c, value in enumerate(row):
                ref = f"{_column(c)}{r}"
                if value is None:
                    cells.append(f'<c r="{ref}"/>')
                elif isinstance(value, int):
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    strings.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
            cells_by_row.append(f'<row r="{r}">{"".join(cells)}</row>')
        sheet_xml.append(
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(cells_by_row)}</sheetData></worksheet>"
        )
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel_ns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    sheet_entries = "".join(
        f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, name in enumerate(sheets, start=1)
    )
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            f"<workbook {ns} {rel_ns}><sheets>{sheet_entries}</sheets></workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>",
        )
        z.writestr(
            "xl/sharedStrings.xml",
            f"<sst {ns}>" + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>",
        )
        for i, xml in enumerate(sheet_xml, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
    return buffer.getvalue()


_LD1_POS1 = "Legislative District 1 - State Representative Pos. 1"
_SUMMARY = [
    _HEADER,
    ("Initiative Measure No. IP26-645", "7325", "Yes", "Yes-1-", None, 830106),
    (_LD1_POS1, "186957", "Ballots Cast", "", None, 45236),
    (_LD1_POS1, "186957", "Davina Duerr", "1601909", "(Prefers Democratic Party)", 32579),
    (_LD1_POS1, "186957", "Maggie Wang", "1601910", "(Prefers Republican Party)", 11692),
    (_LD1_POS1, "186957", "Write-in", "9910", None, 41),
    (_LD1_POS1, "186957", "Over Votes", "", None, 20),
    (_LD1_POS1, "186957", "Under Votes", "", None, 355),
    ("Congressional District 1 - U.S. Representative", "1", "Someone", "77", None, 9),
]
_PRECINCT = [
    ("Precinct", *_HEADER),
    ("Adams County", _LD1_POS1, "186957", "Davina Duerr", "1", None, 3),
]


def test_legislative_rows_carry_the_results_csv_keys() -> None:
    rows = legislative_rows(_workbook({"Summary Results": _SUMMARY, "Precinct Results": _PRECINCT}))
    assert rows[0] == {
        "Race": _LD1_POS1,
        "Candidate": "Davina Duerr",
        "Party": "(Prefers Democratic Party)",
        "Votes": "32579",
        "PercentageOfTotalVotes": "73.52",
        "JurisdictionName": "Legislative",
    }


def test_aggregates_and_other_offices_are_dropped_write_in_kept() -> None:
    rows = legislative_rows(_workbook({"Summary Results": _SUMMARY}))
    # Ballots Cast / Over / Under carry no Choice ID; the measure and the U.S. House seat sit
    # outside a legislative district. Write-in is a real choice; the normalizer drops it.
    assert [r["Candidate"] for r in rows] == ["Davina Duerr", "Maggie Wang", "Write-in"]
    assert rows[1]["Party"] == "(Prefers Republican Party)"
    assert rows[2]["Party"] == ""


def test_the_summary_sheet_is_found_by_its_header_not_its_position() -> None:
    # The per-precinct sheet first: its leading Precinct column marks it as the breakdown.
    wire = _workbook({"Precinct Results": _PRECINCT, "Summary Results": _SUMMARY})
    assert [r["Votes"] for r in legislative_rows(wire)] == ["32579", "11692", "41"]


def test_a_workbook_without_a_summary_sheet_is_unreadable() -> None:
    with pytest.raises(WorkbookUnreadable):
        legislative_rows(_workbook({"Precinct Results": _PRECINCT}))


def test_is_workbook_reads_the_wires_own_magic() -> None:
    assert is_workbook(_workbook({"Summary Results": _SUMMARY}))
    assert not is_workbook(b'"Race","Candidate"\r\n')


def test_the_2026_general_workbook_seats_every_district() -> None:
    rows = legislative_rows(FIXTURE.read_bytes())
    positions = build_house_positions(rows)
    assert sorted(positions) == list(range(1, 50))
    assert all(
        {p.qualifier for p in seats} == {"Position 1", "Position 2"} for seats in positions.values()
    )
    # The 24 Senate seats up in 2026, each with a winner on the election-night count.
    senate = {r["Race"] for r in rows if "senator" in r["Race"].lower()}
    assert len(senate) == 24
    assert len(build_senate_winners(rows)) == 24
    assert all(r["JurisdictionName"] == "Legislative" for r in rows)


def test_a_member_past_the_size_cap_is_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The decompressed-size cap is what stands between a hostile upstream and a zip bomb."""
    monkeypatch.setattr(workbook, "MAX_MEMBER_BYTES", 64)
    with pytest.raises(WorkbookUnreadable, match="size cap"):
        legislative_rows(_workbook({"Summary Results": _SUMMARY}))


def _rewritten(wire: bytes, member: str, body: bytes) -> bytes:
    """``wire`` with one member's bytes replaced."""
    source = zipfile.ZipFile(io.BytesIO(wire))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as out:
        for info in source.infolist():
            out.writestr(info.filename, body if info.filename == member else source.read(info))
    return buffer.getvalue()


def test_malformed_member_xml_is_unreadable() -> None:
    wire = _rewritten(_workbook({"Summary Results": _SUMMARY}), "xl/workbook.xml", b"<workbook")
    with pytest.raises(WorkbookUnreadable, match="xl/workbook.xml"):
        legislative_rows(wire)


def test_a_shared_string_index_past_the_table_is_unreadable() -> None:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    wire = _rewritten(
        _workbook({"Summary Results": _SUMMARY}),
        "xl/sharedStrings.xml",
        f'<sst xmlns="{ns}"/>'.encode(),
    )
    with pytest.raises(WorkbookUnreadable, match="shared string"):
        legislative_rows(wire)
