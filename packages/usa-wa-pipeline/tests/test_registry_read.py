"""The sync registry-read seam for dbt Python models (#309)."""

import pytest

from clearinghouse_core.config import get_settings
from clearinghouse_core.registry import KIND_PERSON, RegistryEntity, apply_decision, decide
from usa_wa_pipeline.registry_read import crosswalk_frame, crosswalk_rows

pytestmark = pytest.mark.db


async def test_crosswalk_rows_flatten_keys_and_tombstones(db_session) -> None:
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


def test_frame_is_empty_only_under_the_hermetic_marker(monkeypatch) -> None:
    """CR 2: the empty fallback is opt-in. Without the marker, a missing
    DATABASE_URL fails the build loudly instead of publishing empty identity."""
    monkeypatch.setenv("USA_WA_PIPELINE_HERMETIC", "1")
    assert crosswalk_frame(KIND_PERSON) == []

    monkeypatch.delenv("USA_WA_PIPELINE_HERMETIC", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()  # the settings cache may hold an env-loaded DSN
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            crosswalk_frame(KIND_PERSON)
    finally:
        get_settings.cache_clear()  # never leak the env-less settings to later tests
