"""The PDC subset-parity probe (#307): its empty-oracle guard (#302 CR).

A subset probe with an empty canonical side passes vacuously — and an empty
oracle means a misconfigured DSN or scheme string, never "nothing to check".
The probe must degrade (exit 4), exactly as it does for an empty store.
"""

from types import SimpleNamespace

import pytest

from clearinghouse_core.rawstore import RawStore
from usa_wa_pipeline.parity_pdc import SOURCE, _parity_job

pytestmark = pytest.mark.db


class _Ctx:
    def __init__(self, session, root) -> None:
        self._session = session
        self.args = SimpleNamespace(root=str(root))

    def require_session(self):
        return self._session


async def test_empty_canonical_oracle_degrades_not_passes(db_session, tmp_path) -> None:
    store = RawStore(tmp_path, SOURCE)
    run = store.open_run()
    run.record("house-winners:2024", b'[{"person_id": "123"}]', url="u")
    run.close()

    result = await _parity_job(_Ctx(db_session, tmp_path))
    assert result.outcome == "degraded"
    assert result.counters["empty_canonical"] is True


async def test_empty_store_still_degrades(db_session, tmp_path) -> None:
    result = await _parity_job(_Ctx(db_session, tmp_path))
    assert result.outcome == "degraded"
    assert result.counters["empty_store"] is True
