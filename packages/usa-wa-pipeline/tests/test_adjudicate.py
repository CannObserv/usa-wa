"""The adjudication CLI's core (#308): merge + move, each leaving a record."""

import pytest
from sqlalchemy import select

from clearinghouse_core.registry import (
    KIND_PERSON,
    RegistryAdjudication,
    apply_decision,
    decide,
    registered_view,
)
from usa_wa_pipeline.adjudicate import adjudicate_merge, adjudicate_move, adjudicate_unmerge

pytestmark = pytest.mark.db


async def _two_entities(db_session) -> tuple[str, str]:
    a = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:1"}), {}), registered_by="test"
    )
    b = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"roster:x:1901"}), {}), registered_by="test"
    )
    return a, b


async def test_merge_sets_tombstone_and_records(db_session) -> None:
    a, b = await _two_entities(db_session)
    await adjudicate_merge(
        db_session, KIND_PERSON, loser=b, survivor=a, note="same human, #144-style audit"
    )
    view = await registered_view(db_session, KIND_PERSON)
    assert view["roster:x:1901"] == a
    row = (
        await db_session.execute(
            select(RegistryAdjudication).where(RegistryAdjudication.action == "merge")
        )
    ).scalar_one()
    assert str(row.subject_entity_id) == b
    assert str(row.target_entity_id) == a
    assert "144" in row.note


async def test_merge_refuses_self_and_unknown(db_session) -> None:
    a, _ = await _two_entities(db_session)
    with pytest.raises(ValueError):
        await adjudicate_merge(db_session, KIND_PERSON, loser=a, survivor=a, note="n")
    with pytest.raises(ValueError):
        await adjudicate_merge(
            db_session, KIND_PERSON, loser="01J9ZQ7X8K3M4N5P6Q7R8S9T0V", survivor=a, note="n"
        )


async def test_move_repoints_one_key(db_session) -> None:
    a, b = await _two_entities(db_session)
    await adjudicate_move(
        db_session, KIND_PERSON, natural_key="roster:x:1901", to_entity=a, note="wrong join"
    )
    view = await registered_view(db_session, KIND_PERSON)
    assert view["roster:x:1901"] == a
    row = (
        await db_session.execute(
            select(RegistryAdjudication).where(RegistryAdjudication.action == "move")
        )
    ).scalar_one()
    assert row.natural_key == "roster:x:1901"


async def test_reverse_merge_is_refused_not_a_cycle(db_session) -> None:
    """Correcting A→B with B→A must be refused (CR 1): a tombstone cycle drops
    BOTH entities from conformed and loops the published crosswalk. The
    correction path is unmerge."""
    a, b = await _two_entities(db_session)
    await adjudicate_merge(db_session, KIND_PERSON, loser=a, survivor=b, note="wrong")
    with pytest.raises(ValueError, match="tombstoned"):
        await adjudicate_merge(db_session, KIND_PERSON, loser=b, survivor=a, note="undo")


async def test_unmerge_clears_tombstone_and_records(db_session) -> None:
    """The sanctioned recovery for a wrong merge: clear the tombstone, keep the
    trail whole."""
    a, b = await _two_entities(db_session)
    await adjudicate_merge(db_session, KIND_PERSON, loser=b, survivor=a, note="wrong")
    await adjudicate_unmerge(db_session, KIND_PERSON, entity=b, note="distinct after audit")
    view = await registered_view(db_session, KIND_PERSON)
    assert view["roster:x:1901"] == b  # resolves to itself again
    row = (
        await db_session.execute(
            select(RegistryAdjudication).where(RegistryAdjudication.action == "unmerge")
        )
    ).scalar_one()
    assert str(row.subject_entity_id) == b
    assert str(row.target_entity_id) == a  # the survivor it was merged into


async def test_unmerge_refuses_a_live_entity(db_session) -> None:
    a, _ = await _two_entities(db_session)
    with pytest.raises(ValueError, match="not merged"):
        await adjudicate_unmerge(db_session, KIND_PERSON, entity=a, note="n")


async def test_move_refuses_tombstoned_destination(db_session) -> None:
    """A typo'd --to landing on a merged-away entity must fail loudly (CR 22)."""
    a, b = await _two_entities(db_session)
    await adjudicate_merge(db_session, KIND_PERSON, loser=b, survivor=a, note="merge")
    with pytest.raises(ValueError, match="tombstoned"):
        await adjudicate_move(
            db_session, KIND_PERSON, natural_key="wsl:1", to_entity=b, note="typo"
        )


async def test_chain_merge_resolves_to_terminal_survivor(db_session) -> None:
    """A→B then B→C is legal (each merge's survivor was live at the time) and
    every key resolves to C; the crosswalk carries both tombstones (CR 31f)."""
    a, b = await _two_entities(db_session)
    c = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"pdc:7"}), {}), registered_by="test"
    )
    await adjudicate_merge(db_session, KIND_PERSON, loser=a, survivor=b, note="a is b")
    await adjudicate_merge(db_session, KIND_PERSON, loser=b, survivor=c, note="b is c")
    view = await registered_view(db_session, KIND_PERSON)
    assert view["wsl:1"] == c
    assert view["roster:x:1901"] == c
    assert view["pdc:7"] == c


async def test_unmerge_inventories_keys_moved_away(db_session) -> None:
    """CR 41: unmerge names the keys whose move adjudications took them off
    this entity, so the operator has the move-back inventory mid-incident
    instead of hand-mining registry.adjudications."""
    a, b = await _two_entities(db_session)
    # the normal merge shape: b's key moved onto a, then b tombstoned into a
    await adjudicate_move(
        db_session, KIND_PERSON, natural_key="roster:x:1901", to_entity=a, note="merge move"
    )
    await adjudicate_merge(db_session, KIND_PERSON, loser=b, survivor=a, note="merge")

    moved_away = await adjudicate_unmerge(
        db_session, KIND_PERSON, entity=b, note="distinct after audit"
    )
    assert moved_away == ["roster:x:1901"]


async def test_unmerge_with_no_moves_reports_empty_inventory(db_session) -> None:
    a, b = await _two_entities(db_session)
    await adjudicate_merge(db_session, KIND_PERSON, loser=b, survivor=a, note="merge")
    assert await adjudicate_unmerge(db_session, KIND_PERSON, entity=b, note="undo") == []
