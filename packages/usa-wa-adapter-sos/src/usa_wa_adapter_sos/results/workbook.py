"""Pure ``results.votewa.gov`` *All Results* workbook → results-CSV rows (#575).

From the 2026 elections the WA SOS publishes results on a vendor platform at ``results.votewa.gov``
whose only bulk export is an ``.xlsx`` (the old site's per-office CSV is gone for those years). Its
``Summary Results`` sheet carries **every** contest — measures, Congress, judicial — one row per
choice under the columns ``Office Name`` / ``Contest ID`` / ``Ballot Name`` / ``Choice ID`` /
``Party`` / ``Total``, plus ``Ballots Cast`` / ``Over Votes`` / ``Under Votes`` aggregate rows that
carry no ``Choice ID``. A ``Precinct Results`` sheet repeats it per precinct.

:func:`legislative_rows` reduces that to the rows the old Legislative CSV carried and maps them
onto its keys, so the staging model and :mod:`usa_wa_adapter_sos.results.normalize` read either
wire unchanged: ``Office Name`` → ``Race`` (the label keeps its old shape,
``Legislative District 1 - State Representative Pos. 1``), ``Ballot Name`` → ``Candidate``,
``Total`` → ``Votes``, with ``PercentageOfTotalVotes`` derived the way the CSV computed it (a
choice's share of its contest's choices, write-ins included) and ``JurisdictionName`` the
``Legislative`` every CSV row carried.

Read with the stdlib (``zipfile`` + ``xml.etree``) — the sheet is plain shared-string and number
cells, so a workbook library would be a dependency for a few dozen lines. The XML is upstream's, so
every member passes :data:`MAX_MEMBER_BYTES` (a zip bomb) before :func:`_parse_xml` (whose expat
caps entity expansion and never resolves an external entity). Never keyed on exact
upstream strings: the summary sheet is the one whose header names an office and no precinct, its
columns are found by keyword, and a legislative row is one naming a legislative district.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

#: The leading bytes of every zip container, ``.xlsx`` included.
_ZIP_MAGIC = b"PK\x03\x04"

#: The most any one workbook member may decompress to. The 2026 General's largest, the
#: per-precinct sheet, is about 1 MB.
MAX_MEMBER_BYTES = 64 * 1024 * 1024

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"

#: A contest inside a legislative district — the old CSV's whole scope.
_LEGISLATIVE_DISTRICT = re.compile(r"LEGISLATIVE DISTRICT\s+\d+", re.IGNORECASE)

#: Header keyword → the summary column it locates (lower-cased substring match).
_COLUMNS = {
    "office": "office",
    "candidate": "ballot",
    "choice": "choice",
    "party": "party",
    "votes": "total",
}

#: The column letters of a cell reference (``"BZ12"`` → ``"BZ"``).
_CELL_COLUMN = re.compile(r"[A-Z]+")

Row = list[str | None]


class WorkbookUnreadable(ValueError):
    """The wire is not a workbook with a summary sheet this module can read."""


def is_workbook(wire: bytes) -> bool:
    """Whether an archived results wire is a workbook — its own magic, not a content type."""
    return wire.startswith(_ZIP_MAGIC)


def legislative_rows(wire: bytes) -> list[dict[str, str | None]]:
    """The workbook's legislative choice rows under the results-CSV keys, in sheet order.

    Raises :class:`WorkbookUnreadable` when the wire is no workbook or has no summary sheet."""
    columns, rows = _summary_table(wire)
    kept = [
        row
        for row in rows
        if _LEGISLATIVE_DISTRICT.search(_cell(row, columns["office"]))
        and _cell(row, columns["choice"])
    ]
    totals: dict[str, int] = {}
    for row in kept:
        race = _cell(row, columns["office"])
        totals[race] = totals.get(race, 0) + (_count(_cell(row, columns["votes"])) or 0)
    return [_csv_row(row, columns, totals) for row in kept]


def _csv_row(row: Row, columns: dict[str, int], totals: dict[str, int]) -> dict[str, str | None]:
    """One summary row under the results-CSV keys."""
    race = _cell(row, columns["office"])
    votes = _count(_cell(row, columns["votes"]))
    total = totals[race]
    return {
        "Race": race,
        "Candidate": _cell(row, columns["candidate"]),
        "Party": _cell(row, columns["party"]),
        "Votes": None if votes is None else str(votes),
        "PercentageOfTotalVotes": f"{votes / total * 100:.2f}"
        if votes is not None and total
        else None,
        "JurisdictionName": "Legislative",
    }


def _cell(row: Row, index: int) -> str:
    """A row's stripped cell text, ``""`` when the cell is absent or empty."""
    value = row[index] if index < len(row) else None
    return (value or "").strip()


def _count(text: str) -> int | None:
    """A vote total as an int (a number cell may read ``"41"`` or ``"41.0"``), else ``None``."""
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def _summary_table(wire: bytes) -> tuple[dict[str, int], list[Row]]:
    """The summary sheet's column indexes and its data rows."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(wire))
    except zipfile.BadZipFile as exc:
        raise WorkbookUnreadable("results wire is not a workbook") from exc
    with archive:
        strings = _shared_strings(archive)
        for path in _sheet_paths(archive):
            rows = _sheet_rows(archive, path, strings)
            columns = _summary_columns(rows[0]) if rows else None
            if columns is not None:
                return columns, rows[1:]
    raise WorkbookUnreadable("no summary sheet (an office column and no precinct column)")


def _summary_columns(header: Row) -> dict[str, int] | None:
    """Each wanted column's index in a header row, or ``None`` if this is not the summary sheet."""
    names = [(cell or "").strip().lower() for cell in header]
    if any("precinct" in name for name in names):
        return None
    columns = {}
    for key, keyword in _COLUMNS.items():
        index = next((i for i, name in enumerate(names) if keyword in name), None)
        if index is None:
            return None
        columns[key] = index
    return columns


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """The workbook's shared-string table (a rich-text item's runs joined)."""
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _read_xml(archive, "xl/sharedStrings.xml")
    return [
        "".join(t.text or "" for t in item.iter(f"{_MAIN}t")) for item in root.iter(f"{_MAIN}si")
    ]


def _sheet_paths(archive: zipfile.ZipFile) -> list[str]:
    """The worksheets' archive paths, in the workbook's sheet order."""
    targets = {
        rel.get("Id"): rel.get("Target", "")
        for rel in _read_xml(archive, "xl/_rels/workbook.xml.rels").iter(_PKG_REL)
    }
    paths = []
    for sheet in _read_xml(archive, "xl/workbook.xml").iter(f"{_MAIN}sheet"):
        target = targets.get(sheet.get(_REL_ID), "").lstrip("/")
        paths.append(target if target.startswith("xl/") else f"xl/{target}")
    return paths


def _sheet_rows(archive: zipfile.ZipFile, path: str, strings: list[str]) -> list[Row]:
    """A worksheet's rows as cell-text lists, placed by each cell's column reference."""
    rows = []
    for row in _read_xml(archive, path).iter(f"{_MAIN}row"):
        cells: Row = []
        for cell in row.iter(f"{_MAIN}c"):
            match = _CELL_COLUMN.match(cell.get("r", ""))
            index = _column_index(match.group(0)) if match else len(cells)
            cells.extend([None] * (index + 1 - len(cells)))
            cells[index] = _cell_text(cell, strings)
        rows.append(cells)
    return rows


def _column_index(letters: str) -> int:
    """Zero-based index of a column's letters (``"A"`` → 0, ``"AA"`` → 26)."""
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - 64
    return index - 1


def _cell_text(cell: ElementTree.Element, strings: list[str]) -> str | None:
    """A cell's text: a shared or inline string, else its stored value."""
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(f"{_MAIN}t"))
    value = cell.find(f"{_MAIN}v")
    if value is None or value.text is None:
        return None
    return strings[int(value.text)] if kind == "s" else value.text


def _read_xml(archive: zipfile.ZipFile, path: str) -> ElementTree.Element:
    """One workbook member, size-capped, parsed."""
    try:
        info = archive.getinfo(path)
    except KeyError as exc:
        raise WorkbookUnreadable(f"workbook has no {path}") from exc
    if info.file_size > MAX_MEMBER_BYTES:
        raise WorkbookUnreadable(f"{path} decompresses past the size cap ({info.file_size} bytes)")
    with archive.open(info) as member:
        data = member.read(MAX_MEMBER_BYTES + 1)
    if len(data) > MAX_MEMBER_BYTES:  # a header that understates its member
        raise WorkbookUnreadable(f"{path} decompresses past the size cap")
    return _parse_xml(data)


def _parse_xml(data: bytes) -> ElementTree.Element:
    """Parse upstream XML. expat (2.4+) caps entity expansion and ElementTree resolves no
    external entity, so the size cap above is the remaining guard."""
    return ElementTree.fromstring(data)  # noqa: S314 — expat entity-hardened; size-capped above
