"""Registry table behavior (#308): apply, seed-id override, merge resolution."""

import pytest

from clearinghouse_core.registry import (
    KIND_PERSON,
    RegistryEntity,
    apply_decision,
    decide,
    registered_view,
)

pytestmark = pytest.mark.db


async def test_mint_append_roundtrip(db_session) -> None:
    view = await registered_view(db_session, KIND_PERSON)
    assert view == {}
    entity_id = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:1"}), view), registered_by="test"
    )
    view = await registered_view(db_session, KIND_PERSON)
    assert view == {"wsl:1": entity_id}

    appended = await apply_decision(
        db_session,
        KIND_PERSON,
        decide(frozenset({"wsl:1", "pdc:9"}), view),
        registered_by="test",
    )
    assert appended == entity_id
    assert (await registered_view(db_session, KIND_PERSON))["pdc:9"] == entity_id


async def test_seed_preserves_canonical_ulid(db_session) -> None:
    canonical = "01J9ZQ7X8K3M4N5P6Q7R8S9T0V"
    minted = await apply_decision(
        db_session,
        KIND_PERSON,
        decide(frozenset({"wsl:1"}), {}),
        registered_by="seed",
        entity_id=canonical,
    )
    assert minted == canonical


async def test_merge_tombstone_resolves_to_survivor(db_session) -> None:
    a = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"wsl:1"}), {}), registered_by="test"
    )
    b = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"roster:x:1901"}), {}), registered_by="test"
    )
    loser = await db_session.get(RegistryEntity, b)
    loser.merged_into = a
    await db_session.flush()

    view = await registered_view(db_session, KIND_PERSON)
    assert view["roster:x:1901"] == a
    assert view["wsl:1"] == a
    # and the conflict the merge resolved no longer conflicts
    assert decide(frozenset({"wsl:1", "roster:x:1901"}), view).action == "noop"
