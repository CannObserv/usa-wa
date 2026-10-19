"""`persons.name_own`: a person's own name where the roster prints a married-name form (#384)."""

import pytest

from usa_wa_pipeline.conformed.entities import PERSON_COLUMNS, person_rows
from usa_wa_pipeline.conformed.own_names import (
    OWN_NAME_CLASSES,
    OWN_NAMES,
    OwnName,
    unmatched_own_names,
)


def _crosswalk(entity: str, key: str) -> dict:
    return {
        "entity_id": entity,
        "key_namespace": "usa_wa_legislature_roster",
        "key_value": key,
        "merged_into": None,
    }


def _roster(name: str, year: int) -> dict:
    return {"year": year, "name": name, "district": 3, "chamber": "house"}


def test_the_table_is_the_decided_set() -> None:
    """#384's 11, plus Marjorie B. Happy (class C), registered after it in #514."""
    assert len(OWN_NAMES) == 12
    assert len({e.roster_key for e in OWN_NAMES}) == 12
    by_class = {c: sorted(e.own for e in OWN_NAMES if e.name_class == c) for c in "AC"}
    assert by_class["A"] == [
        "Agnes Kehoe",
        "Belle Reeves",
        "Frances Swayze",
        "Margaret Hurley",
        "Mary LeCocq",
        "Matilda Jones",
        "Nettie Luella Smith",
    ]
    assert [e.printed for e in OWN_NAMES if e.name_class == "B"] == [
        "Mrs. Harry John Miller",
        "Mrs. John W. Epton",
    ]
    assert by_class["C"] == ["Eva Anderson", "Marjorie B. Happy", "Nellie Tucker"]


def test_class_b_is_null_and_every_other_class_is_not() -> None:
    """#384: class B prints no name of her own, and none is invented."""
    for entry in OWN_NAMES:
        assert (entry.own is None) == (entry.name_class == "B"), entry
        assert entry.evidence.strip()
    assert OWN_NAME_CLASSES == ("A", "B", "C")


def test_an_entry_is_refused_without_evidence_or_with_an_unknown_class() -> None:
    with pytest.raises(ValueError, match="evidence"):
        OwnName("x:1900", "Mrs. X", "X", "A", "")
    with pytest.raises(ValueError, match="class"):
        OwnName("x:1900", "Mrs. X", "X", "D", "e")
    with pytest.raises(ValueError, match="class B"):
        OwnName("x:1900", "Mrs. X", "X", "B", "e")


def test_name_own_is_the_last_column() -> None:
    assert PERSON_COLUMNS == ["entity_id", "name_full", "name_source", "name_own"]


def test_the_entry_names_its_person_and_nobody_else() -> None:
    table = (OwnName("irwinlecocq:1953", "Mrs. Irwin LeCocq (Mary)", "Mary LeCocq", "A", "e"),)
    crosswalk = [_crosswalk("01A", "irwinlecocq:1953"), _crosswalk("01B", "other:1953")]
    roster = [_roster("Mrs. Irwin LeCocq (Mary)", 1953), _roster("Other Person", 1953)]
    rows = person_rows(crosswalk, sponsors=[], roster=roster, pdc=[], own_names=table)
    by_id = {r["entity_id"]: r for r in rows}
    assert by_id["01A"]["name_own"] == "Mary LeCocq"
    assert by_id["01A"]["name_full"] == "Mrs. Irwin LeCocq (Mary)", "name_full is untouched"
    assert by_id["01B"]["name_own"] is None
    assert unmatched_own_names(rows, crosswalk, table) == []


def test_a_reprinted_form_withholds_the_value_and_is_reported() -> None:
    """An entry pins the printed form it adjudicated: a new edition printing another
    form gets no stale value, and the model fails loudly on it."""
    table = (OwnName("irwinlecocq:1953", "Mrs. Irwin LeCocq (Mary)", "Mary LeCocq", "A", "e"),)
    crosswalk = [_crosswalk("01A", "irwinlecocq:1953")]
    rows = person_rows(
        crosswalk, sponsors=[], roster=[_roster("Mrs. Irwin LeCocq", 1953)], pdc=[], own_names=table
    )
    assert rows[0]["name_own"] is None
    assert unmatched_own_names(rows, crosswalk, table) == list(table)


def test_an_entry_whose_key_is_unregistered_is_reported() -> None:
    table = (OwnName("gone:1953", "Mrs. Gone", "Gone", "C", "e"),)
    rows = person_rows([], sponsors=[], roster=[], pdc=[], own_names=table)
    assert unmatched_own_names(rows, [], table) == list(table)
