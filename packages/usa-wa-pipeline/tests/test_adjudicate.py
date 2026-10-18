"""The adjudication CLI's core (#308): merge, move, unmerge, register (#514) — each recorded."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from clearinghouse_core.registry import (
    KIND_PERSON,
    RegistryAdjudication,
    RegistryKey,
    apply_decision,
    decide,
    registered_view,
)
from usa_wa_pipeline.adjudicate import (
    _adjudicate_job,
    adjudicate_merge,
    adjudicate_move,
    adjudicate_register,
    adjudicate_unmerge,
)

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


async def test_unmerge_inventory_excludes_keys_moved_back(db_session) -> None:
    """CR 52: a key moved away and later moved BACK is bound to this entity
    again — listing it would invite a wrong move mid-incident."""
    a, b = await _two_entities(db_session)
    await adjudicate_move(
        db_session, KIND_PERSON, natural_key="roster:x:1901", to_entity=a, note="away"
    )
    await adjudicate_move(
        db_session, KIND_PERSON, natural_key="roster:x:1901", to_entity=b, note="back"
    )
    await adjudicate_merge(db_session, KIND_PERSON, loser=b, survivor=a, note="merge")
    assert await adjudicate_unmerge(db_session, KIND_PERSON, entity=b, note="undo") == []


async def test_unmerge_cli_reports_the_inventory(db_session, monkeypatch) -> None:
    """CR 51: the operator's interface must carry the keys, not just the log."""
    a, b = await _two_entities(db_session)
    await adjudicate_move(
        db_session, KIND_PERSON, natural_key="roster:x:1901", to_entity=a, note="merge move"
    )
    await adjudicate_merge(db_session, KIND_PERSON, loser=b, survivor=a, note="merge")

    ctx = SimpleNamespace(
        args=SimpleNamespace(
            verb="unmerge",
            kind=KIND_PERSON,
            note="undo",
            entity=b,
            loser=None,
            survivor=None,
            key=None,
            to_entity=None,
        ),
        require_session=lambda: db_session,
    )
    result = await _adjudicate_job(ctx)
    assert result.counters["action"] == "unmerge"
    assert result.counters["keys_moved_away"] == ["roster:x:1901"]


# ---------------------------------------------------------------------------
# #514 — register: the one path that mints an entity for a key the registrar won't


_NEW_ROSTER_KEY = "usa_wa_legislature_roster:marjoriebhappy:1959"


async def test_register_mints_one_entity_for_an_unregistered_key(db_session) -> None:
    """A roster key is pair-only (the registrar never mints one), so a parser fix that
    surfaces a new roster-only person needs a recorded human decision to bind it."""
    entity = await adjudicate_register(
        db_session, KIND_PERSON, natural_key=_NEW_ROSTER_KEY, note="roster p.34, #514"
    )
    view = await registered_view(db_session, KIND_PERSON)
    assert view[_NEW_ROSTER_KEY] == entity
    row = (
        await db_session.execute(
            select(RegistryAdjudication).where(RegistryAdjudication.action == "register")
        )
    ).scalar_one()
    assert str(row.target_entity_id) == entity
    assert row.subject_entity_id is None
    assert row.natural_key == _NEW_ROSTER_KEY
    assert "#514" in row.note
    key = (
        await db_session.execute(
            select(RegistryKey).where(RegistryKey.natural_key == _NEW_ROSTER_KEY)
        )
    ).scalar_one()
    assert key.registered_by == f"adjudication:{row.id}"


async def test_register_refuses_a_registered_key(db_session) -> None:
    """A bound key is re-pointed with move, never re-minted — a second entity would be a
    published duplicate the registry has no delete for."""
    await _two_entities(db_session)
    with pytest.raises(ValueError, match="already registered"):
        await adjudicate_register(db_session, KIND_PERSON, natural_key="roster:x:1901", note="dup")


async def test_register_cli_reports_the_minted_entity(db_session) -> None:
    """The operator needs the new ULID (a seed row names the span_key it starts)."""
    ctx = SimpleNamespace(
        args=SimpleNamespace(
            verb="register",
            kind=KIND_PERSON,
            note="roster p.34",
            entity=None,
            loser=None,
            survivor=None,
            key=_NEW_ROSTER_KEY,
            to_entity=None,
        ),
        require_session=lambda: db_session,
    )
    result = await _adjudicate_job(ctx)
    view = await registered_view(db_session, KIND_PERSON)
    assert result.counters == {"action": "register", "entity": view[_NEW_ROSTER_KEY]}
