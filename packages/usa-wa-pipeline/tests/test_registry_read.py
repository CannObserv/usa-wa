"""The sync registry-read seam for dbt Python models (#309)."""

import pytest

from clearinghouse_core.registry import KIND_PERSON, apply_decision, decide
from usa_wa_pipeline.registry_read import crosswalk_rows

pytestmark = pytest.mark.db


async def test_crosswalk_rows_flatten_keys_and_tombstones(db_session) -> None:
    from clearinghouse_core.registry import RegistryEntity

    a = await apply_decision(
        db_session,
        KIND_PERSON,
        decide(frozenset({"usa_wa_legislature:1", "wa_pdc:9"}), {}),
        registered_by="test",
    )
    b = await apply_decision(
        db_session, KIND_PERSON, decide(frozenset({"roster:x:1901"}), {}), registered_by="test"
    )
    (await db_session.get(RegistryEntity, b)).merged_into = a
    await db_session.flush()

    rows = await crosswalk_rows(db_session, KIND_PERSON)
    by_key = {r["natural_key"]: r for r in rows}
    assert by_key["usa_wa_legislature:1"]["entity_id"] == a
    assert by_key["usa_wa_legislature:1"]["key_namespace"] == "usa_wa_legislature"
    assert by_key["usa_wa_legislature:1"]["key_value"] == "1"
    assert by_key["usa_wa_legislature:1"]["merged_into"] is None
    # the tombstoned entity's key still lists, carrying the merge signal
    assert by_key["roster:x:1901"]["entity_id"] == b
    assert by_key["roster:x:1901"]["merged_into"] == a
