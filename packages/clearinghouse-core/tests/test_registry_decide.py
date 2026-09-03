"""The registrar decision table (#308), as a pure function.

The replatform spec's five rows: mint / append / no-op / conflict (≥2 entities)
/ sticky (a registered key proposed elsewhere moves nothing). Absence never
decays membership — that is a staging fact, not an identity fact.
"""

from clearinghouse_core.registry import decide


def test_all_unknown_mints() -> None:
    decision = decide(frozenset({"wsl:1", "roster:x:1901"}), {})
    assert decision.action == "mint"
    assert decision.keys_to_register == frozenset({"wsl:1", "roster:x:1901"})
    assert decision.entity_id is None  # minting assigns the id at apply time


def test_partial_overlap_appends_to_the_one_entity() -> None:
    decision = decide(
        frozenset({"wsl:1", "pdc:7710"}),
        {"wsl:1": "01ENTITY"},
    )
    assert decision.action == "append"
    assert decision.entity_id == "01ENTITY"
    assert decision.keys_to_register == frozenset({"pdc:7710"})


def test_full_overlap_is_noop() -> None:
    decision = decide(frozenset({"wsl:1"}), {"wsl:1": "01ENTITY"})
    assert decision.action == "noop"
    assert decision.keys_to_register == frozenset()


def test_two_entities_is_conflict_no_write() -> None:
    decision = decide(
        frozenset({"wsl:1", "roster:x:1901"}),
        {"wsl:1": "01A", "roster:x:1901": "01B"},
    )
    assert decision.action == "conflict"
    assert decision.entity_ids == frozenset({"01A", "01B"})
    assert decision.keys_to_register == frozenset()


def test_registry_is_sticky_never_moves_a_key() -> None:
    """A cluster proposing an unknown key alongside one registered elsewhere
    appends to THAT entity — matching proposes, never revokes; and a proposal
    that would re-home a registered key is just its entity's cluster."""
    decision = decide(
        frozenset({"wsl:1", "new:key"}),
        {"wsl:1": "01A"},
    )
    assert decision.action == "append"
    assert decision.entity_id == "01A"
