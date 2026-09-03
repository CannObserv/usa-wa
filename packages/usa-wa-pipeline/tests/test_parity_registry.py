"""The registry parity probe (#308): canonical identity ⊆ the registry.

Pins the exit-gate semantics the nightly chain depends on: clean, missing,
mismapped — and the #302 CR distinction that an adjudicated merge of seeded
entities is sanctioned policy (its own count), never a standing alarm.
"""

from datetime import UTC, datetime

import pytest

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.registry import KIND_PERSON, RegistryEntity, RegistryKey
from clearinghouse_domain_legislative.identity import Person
from usa_wa_pipeline.parity_registry import run_parity

pytestmark = pytest.mark.db

SOURCE = "usa_wa_legislature"


async def _seed_person(db_session, source_id: str) -> str:
    person = Person(source=SOURCE, source_id=source_id, name_full=f"M{source_id}")
    db_session.add(person)
    await db_session.flush()
    return str(person.id)


async def _bind(db_session, natural_key: str, entity_id: str) -> None:
    if (await db_session.get(RegistryEntity, entity_id)) is None:
        db_session.add(RegistryEntity(kind=KIND_PERSON, id=entity_id))
        await db_session.flush()
    db_session.add(
        RegistryKey(
            kind=KIND_PERSON, natural_key=natural_key, entity_id=entity_id, registered_by="test"
        )
    )
    await db_session.flush()


async def _seed_jurisdiction(db_session) -> None:
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()
    db_session.add(
        Jurisdiction(slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC))
    )
    await db_session.flush()


async def test_clean_when_every_key_maps_to_its_own_ulid(db_session) -> None:
    await _seed_jurisdiction(db_session)
    pid = await _seed_person(db_session, "100")
    await _bind(db_session, f"{SOURCE}:100", pid)
    counters, failed = await run_parity(db_session)
    assert not failed
    assert counters["person_missing"] == 0
    assert counters["person_mismapped"] == 0


async def test_unregistered_canonical_row_is_missing(db_session) -> None:
    await _seed_jurisdiction(db_session)
    await _seed_person(db_session, "100")
    counters, failed = await run_parity(db_session)
    assert failed
    assert counters["person_missing"] == 1


async def test_adjudicated_merge_is_counted_not_alarmed(db_session) -> None:
    """A merge of two seeded entities (loser tombstoned, key moved to the
    survivor) is policy: counted as merged, never a standing mismapped."""
    await _seed_jurisdiction(db_session)
    a = await _seed_person(db_session, "1")
    b = await _seed_person(db_session, "2")
    await _bind(db_session, f"{SOURCE}:1", a)
    # b's key was moved onto a's entity by an adjudication; b tombstoned into a
    await _bind(db_session, f"{SOURCE}:2", a)
    entity_b = RegistryEntity(kind=KIND_PERSON, id=b, merged_into=a)
    db_session.add(entity_b)
    await db_session.flush()

    counters, failed = await run_parity(db_session)
    assert not failed
    assert counters["person_merged"] == 1
    assert counters["person_mismapped"] == 0


async def test_binding_to_an_unrelated_entity_is_mismapped(db_session) -> None:
    await _seed_jurisdiction(db_session)
    a = await _seed_person(db_session, "1")
    b = await _seed_person(db_session, "2")
    await _bind(db_session, f"{SOURCE}:1", a)
    await _bind(db_session, f"{SOURCE}:2", a)  # bound elsewhere, no merge chain
    await _bind(db_session, f"{SOURCE}:extra", b)
    counters, failed = await run_parity(db_session)
    assert failed
    assert counters["person_mismapped"] == 1
