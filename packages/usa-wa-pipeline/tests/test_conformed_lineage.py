"""Conformed committee lineage (#447): the published edges, INV2's retired set,
and the time-ordering gate over cycles."""

import duckdb
import pandas as pd

from usa_wa_pipeline.conformed.lineage import (
    LINEAGE_COLUMNS,
    LINEAGE_SCHEMA,
    lineage_rows,
    retired_entities,
    untimely_cycles,
)
from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.operator_read import SuccessionRow


def _key(value: str, entity_id: str, merged_into: str | None = None) -> dict:
    return {
        "entity_id": entity_id,
        "natural_key": f"usa_wa_legislature:{value}",
        "key_namespace": "usa_wa_legislature",
        "key_value": value,
        "registered_by": "test",
        "merged_into": merged_into,
    }


def _link(subject: str, linked: str, slug: str = "succeeded_by", year: int | None = 2001):
    return SuccessionRow(
        subject_source_id=subject,
        linked_source_id=linked,
        slug=slug,
        effective_year=year,
        evidence_url=f"https://example.test/{subject}-{linked}",
        notes=None,
    )


def _edge(subject: str, linked: str, slug: str = "succeeded_by", year: int | None = 2001):
    """A built lineage row, ends already resolved (entity id = 'E' + WSL id)."""
    return {
        "subject_entity_id": f"E{subject}",
        "slug": slug,
        "linked_entity_id": f"E{linked}",
        "subject_source_id": subject,
        "linked_source_id": linked,
        "effective_year": year,
        "evidence_url": "https://example.test/",
        "notes": None,
    }


def test_each_end_resolves_to_its_entity_beside_the_raw_wsl_id() -> None:
    crosswalk = [_key("35341", "01OLD"), _key("36500", "01NEW")]
    link = SuccessionRow("35341", "36500", "succeeded_by", 2026, "https://x.test/8406", "SCR 8406")

    (row,) = lineage_rows(crosswalk, [link])

    assert list(row) == LINEAGE_COLUMNS
    assert row == {
        "subject_entity_id": "01OLD",
        "slug": "succeeded_by",
        "linked_entity_id": "01NEW",
        "subject_source_id": "35341",
        "linked_source_id": "36500",
        "effective_year": 2026,
        "evidence_url": "https://x.test/8406",
        "notes": "SCR 8406",
    }


def test_built_rows_type_under_the_declared_schema() -> None:
    """The model's own cast, over a real year: Python ints infer BIGINT, and
    `typed_relation` refuses a narrowing cast — the first live build did."""
    crosswalk = [_key("35341", "01OLD"), _key("36500", "01NEW")]
    rows = lineage_rows(crosswalk, [_link("35341", "36500", year=2026), _link("1", "2", year=None)])

    relation = typed_relation(duckdb.connect(), rows, LINEAGE_SCHEMA)

    assert relation.fetchall()[0][5] == 2026


def test_an_end_follows_the_merge_tombstone_to_its_survivor() -> None:
    """A merge re-points, it does not delete (#366): the edge publishes on the
    entity a consumer can still resolve."""
    crosswalk = [
        _key("1", "01LOSER", merged_into="01SURV"),
        _key("1001", "01SURV"),
        _key("2", "01B"),
    ]

    (row,) = lineage_rows(crosswalk, [_link("1", "2")])

    assert row["subject_entity_id"] == "01SURV"
    assert row["subject_source_id"] == "1"


def test_an_unregistered_end_publishes_null_for_the_gate_to_refuse() -> None:
    """Never dropped: a dropped link is a silent retraction. The null fails the
    not_null test, and the built table is the hand-review work order."""
    (row,) = lineage_rows([_key("2", "01B")], [_link("999", "2")])

    assert row["subject_entity_id"] is None
    assert row["linked_entity_id"] == "01B"


def test_another_namespace_never_resolves_a_committee_id() -> None:
    """The WSL committee id lives under `usa_wa_legislature`; the same digits in
    another namespace are a different thing."""
    stray = {**_key("2", "01X"), "key_namespace": "wa_pdc", "natural_key": "wa_pdc:2"}

    (row,) = lineage_rows([stray], [_link("2", "3")])

    assert row["subject_entity_id"] is None


def test_succeeded_and_merged_predecessors_retire_and_a_split_child_does_not() -> None:
    """INV2 (#124): the subject of a `succeeded_by` / `merged_with` is retired;
    `split_from` leaves its subject live (a split's two heads)."""
    rows = [
        _edge("1", "2", "succeeded_by"),
        _edge("3", "4", "merged_with"),
        _edge("5", "6", "split_from"),
    ]
    assert retired_entities(rows) == {"E1", "E3"}


def test_an_unresolved_subject_retires_nothing() -> None:
    rows = [{**_edge("1", "2"), "subject_entity_id": None}]
    assert retired_entities(rows) == set()


def test_an_acyclic_lineage_has_no_untimely_cycle() -> None:
    assert untimely_cycles([_edge("1", "2", year=1993), _edge("2", "3", year=1995)]) == []


def test_a_round_trip_in_time_order_is_legitimate() -> None:
    """#126: House Trade & Economic Development 924 → 966 → 924 (1993/1995). A
    recurring WSL id makes a time-ordered cycle real data, not a defect — five
    such cycles are in production."""
    rows = [_edge("924", "966", year=1993), _edge("966", "924", year=1995)]
    assert untimely_cycles(rows) == []


def test_a_three_way_round_trip_in_time_order_is_legitimate() -> None:
    """Production's 1 → 3492 → 8254 → 1 (2001/2003/2005)."""
    rows = [
        _edge("1", "3492", year=2001),
        _edge("3492", "8254", year=2003),
        _edge("8254", "1", year=2005),
    ]
    assert untimely_cycles(rows) == []


def test_a_same_year_reversal_is_untimely() -> None:
    """A succeeded_by B and B succeeded_by A in one year: one of the two is a
    direction error, and no reading of time makes both true."""
    rows = [_edge("1", "2", year=2001), _edge("2", "1", year=2001)]

    (cycle,) = untimely_cycles(rows)

    assert cycle == {
        "source_ids": "1 → 2 → 1",
        "entity_ids": "E1 → E2 → E1",
        "effective_years": "2001 → 2001",
    }


def test_a_cycle_whose_years_wrap_twice_is_untimely() -> None:
    """1 → 2 (2001), 2 → 3 (2005), 3 → 1 (2003): 3 hands back to 1 before 3
    existed. Time order allows exactly one wrap around a cycle."""
    rows = [_edge("1", "2", year=2001), _edge("2", "3", year=2005), _edge("3", "1", year=2003)]
    assert len(untimely_cycles(rows)) == 1


def test_a_cycle_with_an_undated_link_cannot_be_ordered() -> None:
    rows = [_edge("1", "2", year=1993), _edge("2", "1", year=None)]

    (cycle,) = untimely_cycles(rows)

    assert cycle["effective_years"] == "1993 → null"


def test_merged_with_counts_as_forward_flow_and_split_from_does_not() -> None:
    """Both retiring slugs run predecessor → successor. `split_from` runs child →
    parent, so 8265 split_from 438 + 8265 succeeded_by 438 (production) is a
    dormancy blip, not a cycle."""
    merged = [_edge("1", "2", year=2001), _edge("2", "1", "merged_with", year=2001)]
    split = [_edge("8265", "438", "split_from", year=2001), _edge("8265", "438", year=2001)]

    assert len(untimely_cycles(merged)) == 1
    assert untimely_cycles(split) == []


def test_parallel_edges_are_each_checked() -> None:
    """Two A → B links with different years make two distinct cycles with B → A;
    each must be time-ordered on its own."""
    rows = [
        _edge("1", "2", year=1993),
        _edge("1", "2", "merged_with", year=1995),
        _edge("2", "1", year=1995),
    ]
    (cycle,) = untimely_cycles(rows)
    assert cycle["effective_years"] == "1995 → 1995"


def test_self_loops_and_unresolved_ends_are_left_to_their_own_gates() -> None:
    """A link whose ends merged into one entity is `org_lineage_distinct_ends`'s
    to report, a null end the not_null test's; the cycle walk skips both."""
    rows = [
        {**_edge("1", "2"), "linked_entity_id": "E1"},
        {**_edge("3", "4"), "linked_entity_id": None},
    ]
    assert untimely_cycles(rows) == []


def test_years_read_through_pandas_null_and_numeric_shapes() -> None:
    """The model reads `org_lineage` back via `.df()`: a BIGINT column with a
    NULL arrives as float64 (1993.0, NaN), one without as numpy ints. A float
    year read as 'undated' would fail every legitimate round trip."""
    numpy_int = pd.Series([1995]).iloc[0]
    rows = [_edge("924", "966", year=1993.0), _edge("966", "924", year=numpy_int)]
    assert untimely_cycles(rows) == []

    rows = [_edge("1", "2", year=1993.0), _edge("2", "1", year=float("nan"))]
    assert untimely_cycles(rows)[0]["effective_years"] == "1993 → null"
