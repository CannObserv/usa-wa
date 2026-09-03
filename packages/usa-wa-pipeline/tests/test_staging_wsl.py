"""Legislature staging row-builders (#306): raw store wires → natural-key rows.

The logic under the dbt Python models (docs/PIPELINE.md § TDD — models are thin
adapters over these). Parsers are injected; the real offline SOAP parse is the
adapter's contract, cassette-tested there.
"""

from clearinghouse_core.rawstore import RawStore
from usa_wa_pipeline.staging import wsl

BIENNIUM = "2025-26"


def _store_with(tmp_path, resources: dict[str, bytes]) -> RawStore:
    store = RawStore(tmp_path, "usa_wa_legislature")
    run = store.open_run()
    for resource_id, body in resources.items():
        run.record(resource_id, body, url="u")
    run.close()
    return store


def test_committee_rows_from_latest_roster_wires(tmp_path) -> None:
    store = _store_with(
        tmp_path,
        {
            f"committees-roster:{BIENNIUM}": b"wire-a",
            "committees-roster:2023-24": b"wire-b",
            f"sponsors:{BIENNIUM}": b"ignored",
        },
    )

    def parse(wire: bytes):
        return {
            b"wire-a": [{"Id": 1, "Agency": "House", "Name": "Ag", "LongName": "Agriculture"}],
            b"wire-b": [{"Id": 9, "Agency": "Senate", "Name": "WM", "LongName": "Ways"}],
        }[wire]

    rows = wsl.committee_rows(store, parse=parse)
    assert {(r["biennium"], r["committee_id"]) for r in rows} == {
        (BIENNIUM, "1"),
        ("2023-24", "9"),
    }
    [row] = [r for r in rows if r["biennium"] == BIENNIUM]
    assert row["agency"] == "House"
    assert row["name"] == "Ag"
    assert row["long_name"] == "Agriculture"


def test_committee_rows_use_latest_wire_per_resource(tmp_path) -> None:
    """Two runs of the same biennium: only the newest wire is parsed."""
    store = _store_with(tmp_path, {f"committees-roster:{BIENNIUM}": b"old"})
    run = store.open_run()
    run.record(f"committees-roster:{BIENNIUM}", b"new", url="u")
    run.close()

    seen: list[bytes] = []

    def parse(wire: bytes):
        seen.append(wire)
        return []

    wsl.committee_rows(store, parse=parse)
    assert seen == [b"new"]


def test_sponsor_rows(tmp_path) -> None:
    store = _store_with(tmp_path, {f"sponsors:{BIENNIUM}": b"wire"})

    def parse(wire: bytes):
        return [
            {
                "Id": 27992,
                "Name": "Whitfield",
                "LongName": "Dana Whitfield",
                "Agency": "House",
                "Party": "D",
                "District": "14",
                "FirstName": "Dana",
                "LastName": "Whitfield",
            }
        ]

    [row] = wsl.sponsor_rows(store, parse=parse)
    assert row["biennium"] == BIENNIUM
    assert row["member_id"] == "27992"
    assert row["agency"] == "House"
    assert row["party"] == "D"
    assert row["district"] == "14"
    assert row["long_name"] == "Dana Whitfield"


def test_committee_member_rows_key_from_resource_id(tmp_path) -> None:
    rid = f"committee-members-hist:{BIENNIUM}:42:House:Agriculture"
    store = _store_with(
        tmp_path, {rid: b"wire", f"committee-members-hist:{BIENNIUM}:7:Senate:Ways": b""}
    )

    def parse(wire: bytes):
        return [{"Id": 100, "Name": "W.", "LongName": "Dana Whitfield"}] if wire else []

    rows = wsl.committee_member_rows(store, parse=parse)
    [row] = rows  # the empty wire (benign fault archive) contributes nothing
    assert row["biennium"] == BIENNIUM
    assert row["committee_id"] == "42"
    assert row["committee_agency"] == "House"
    assert row["committee_name"] == "Agriculture"
    assert row["member_id"] == "100"


def test_meeting_rows_flatten_committee_refs(tmp_path) -> None:
    store = _store_with(tmp_path, {"committee-meetings:2025-01-01:2026-12-31": b"wire"})

    def parse(wire: bytes):
        return [
            {
                "Agency": "Joint",
                "Committees": {"Committee": {"Id": 5, "Agency": "Joint", "Name": "JTC"}},
            },
            {
                "Agency": "House",
                "Committees": {"Committee": [{"Id": 6, "Agency": "House", "Name": "Ag"}]},
            },
            {"Agency": "Other", "Committees": None},
        ]

    rows = wsl.meeting_rows(store, parse=parse)
    assert {(r["committee_id"], r["committee_agency"]) for r in rows} == {
        ("5", "Joint"),
        ("6", "House"),
    }
    assert all(r["meeting_window"] == "2025-01-01:2026-12-31" for r in rows)
