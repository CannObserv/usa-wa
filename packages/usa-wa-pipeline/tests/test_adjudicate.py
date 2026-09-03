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
from usa_wa_pipeline.adjudicate import adjudicate_merge, adjudicate_move

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
