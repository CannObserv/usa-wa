"""find_succession_cycles — advisory cycle detection over the lineage graph (usa-wa#126)."""

from types import SimpleNamespace

from clearinghouse_domain_legislative.committee_succession import find_succession_cycles


def _link(subject, linked, slug="succeeded_by"):
    return SimpleNamespace(subject_source_id=subject, linked_source_id=linked, slug=slug)


def test_detects_a_two_cycle_and_terminates():
    # 924 -> 966 -> 924 (a round-trip rename); must return the cycle, not hang.
    cycles = find_succession_cycles([_link("924", "966"), _link("966", "924")])
    assert cycles == [["924", "966"]]  # normalised to start at the smallest node


def test_acyclic_chain_has_no_cycles():
    cycles = find_succession_cycles(
        [_link("3494", "8261"), _link("8261", "10170"), _link("10170", "12232")]
    )
    assert cycles == []


def test_split_from_reverses_edge_direction():
    # split_from: linked (parent) -> subject (child). A parent->child->parent round trip.
    cycles = find_succession_cycles(
        [_link("B", "A", slug="split_from"), _link("A", "B", slug="succeeded_by")]
    )
    # edges: A->B (split parent A to child B) and A->B (succeeded) — no cycle (both A->B).
    assert cycles == []


def test_self_consistent_across_multiple_disjoint_cycles():
    cycles = find_succession_cycles(
        [
            _link("924", "966"),
            _link("966", "924"),
            _link("924", "3511"),
            _link("3511", "924"),
        ]
    )
    # Each cycle is normalised to start at its lexicographically-smallest node
    # ("3511" < "924" as strings), giving one deterministic representation.
    assert cycles == [["3511", "924"], ["924", "966"]]


def test_empty_and_single_edge():
    assert find_succession_cycles([]) == []
    assert find_succession_cycles([_link("A", "B")]) == []
