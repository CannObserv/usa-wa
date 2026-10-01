"""Tests for scripts/rehearsal_roster.py — the partial-roster wire the #135 rehearsal builds from.

The rollover's partial case (the new biennium's roster half-published) cannot be fetched
before it happens, so the rehearsal synthesizes it: a subset of the outgoing biennium's
members, recorded as the incoming biennium's ``sponsors:`` wire in a SCRATCH raw store.
These pin that the wire stages exactly as a real one would, that it says what it is, and
that the tool cannot write anywhere but a rehearsal's scratch store.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from clearinghouse_core.rawstore import RawStore
from usa_wa_pipeline.staging.wsl import sponsor_rows

SCRIPT = Path(__file__).parent.parent / "rehearsal_roster.py"

SOURCE = "usa_wa_legislature"


def _member(member_id: int, last: str, agency: str = "House", district: int = 5) -> str:
    return (
        f"<Member><Id>{member_id}</Id><Name>Pat {last}</Name>"
        f"<LongName>Representative {last}</LongName><Agency>{agency}</Agency>"
        f"<Acronym>{last[:4].upper()}</Acronym><Party>D</Party><District>{district}</District>"
        f"<Phone>(360) 786-7000</Phone><Email>{last}@leg.wa.gov</Email>"
        f"<FirstName>Pat</FirstName><LastName>{last}</LastName></Member>"
    )


def _wire(members: list[str]) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?><soap:Envelope '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema"><soap:Body>'
        '<GetSponsorsResponse xmlns="http://WSLWebServices.leg.wa.gov/"><GetSponsorsResult>'
        + "".join(members)
        + "</GetSponsorsResult></GetSponsorsResponse></soap:Body></soap:Envelope>"
    ).encode()


MEMBERS = [_member(1000 + i, f"Member{i:02d}", district=1 + i % 49) for i in range(40)]


@pytest.fixture
def scratch(tmp_path):
    """A rehearsal scratch dir: the marker the wrapper writes, and a raw store holding a
    2025-26 sponsors wire."""
    (tmp_path / ".rehearsal").write_text("2027-28 partial\n")
    store = RawStore(tmp_path / "raw", SOURCE)
    run = store.open_run()
    run.record("sponsors:2025-26", _wire(MEMBERS), url="https://example.invalid/GetSponsors")
    run.close()
    return tmp_path


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _by_biennium(root: Path) -> dict[str, set[str]]:
    rows = sponsor_rows(RawStore(root, SOURCE))
    out: dict[str, set[str]] = {}
    for row in rows:
        out.setdefault(row["biennium"], set()).add(row["member_id"])
    return out


def test_the_partial_wire_stages_as_a_subset_of_the_outgoing_roster(scratch) -> None:
    proc = _run(scratch / "raw", "--from", "2025-26", "--to", "2027-28", "--keep", "0.5")

    assert proc.returncode == 0, proc.stderr
    staged = _by_biennium(scratch / "raw")
    assert staged["2025-26"] == {str(1000 + i) for i in range(40)}
    assert 0 < len(staged["2027-28"]) < 40
    assert staged["2027-28"] < staged["2025-26"]


def test_the_subset_is_deterministic(scratch, tmp_path_factory) -> None:
    """Two rehearsals of the same scenario must build from the same roster."""
    other = tmp_path_factory.mktemp("again")
    (other / ".rehearsal").write_text("2027-28 partial\n")
    store = RawStore(other / "raw", SOURCE)
    run = store.open_run()
    run.record("sponsors:2025-26", _wire(MEMBERS), url="https://example.invalid/GetSponsors")
    run.close()

    for root in (scratch, other):
        _run(root / "raw", "--from", "2025-26", "--to", "2027-28", "--keep", "0.5")
    assert _by_biennium(scratch / "raw")["2027-28"] == _by_biennium(other / "raw")["2027-28"]


def test_the_wire_says_it_was_synthesized(scratch) -> None:
    """Never mistakable for a fetch: its URL and manifest entry name the rehearsal."""
    _run(scratch / "raw", "--from", "2025-26", "--to", "2027-28", "--keep", "0.5")

    store = RawStore(scratch / "raw", SOURCE)
    entries = [
        entry
        for path in store.manifest_paths()
        for entry in json.loads(path.read_text())["entries"]
        if entry["resource_id"] == "sponsors:2027-28"
    ]
    [entry] = entries
    assert entry["url"].startswith("rehearsal://")
    assert entry["rehearsal"] == "partial"
    assert entry["synthesized_from"] == "sponsors:2025-26"


def test_a_root_outside_a_rehearsal_scratch_dir_is_refused(tmp_path) -> None:
    """The production raw store has no `.rehearsal` marker beside it — nothing is written."""
    store = RawStore(tmp_path / "raw", SOURCE)
    run = store.open_run()
    run.record("sponsors:2025-26", _wire(MEMBERS), url="https://example.invalid/GetSponsors")
    run.close()
    before = sorted(p.name for p in store.manifest_paths())

    proc = _run(tmp_path / "raw", "--from", "2025-26", "--to", "2027-28", "--keep", "0.5")

    assert proc.returncode == 2
    assert ".rehearsal" in proc.stderr
    assert sorted(p.name for p in store.manifest_paths()) == before


def test_a_missing_source_wire_is_refused(scratch) -> None:
    proc = _run(scratch / "raw", "--from", "2023-24", "--to", "2027-28", "--keep", "0.5")

    assert proc.returncode == 2
    assert "sponsors:2023-24" in proc.stderr
