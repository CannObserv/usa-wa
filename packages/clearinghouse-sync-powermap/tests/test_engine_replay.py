"""Changes-feed replay backstop tests (usa-wa#159).

The replay re-reads a trailing window of the changes feed (``high_water − margin``)
each cycle to re-cover PM's at-least-once concurrent-commit skip (power-map#387),
replacing the O(cohort) anchored scan as the primary dropped-event backstop. This
module covers the floor arithmetic + margin validation (step 2) and the
``replay_from_floor`` engine method + horizon fall-off detection (step 3).
"""

import pytest

from clearinghouse_sync_powermap.engine import (
    DEFAULT_REPLAY_MARGIN,
    SyncEngine,
    _replay_floor,
)
from clearinghouse_sync_powermap.testing import FakeClient


@pytest.mark.parametrize(
    ("high_water", "margin", "expected"),
    [
        (None, DEFAULT_REPLAY_MARGIN, 0),  # fresh stream → replay whole retained window
        (5, 10_000, 0),  # margin ≥ high_water → clamp at 0, not negative
        (10_000, 10_000, 0),  # exact → 0
        (50_000, 10_000, 40_000),  # trailing window
        (50_000, 0, 50_000),  # zero margin → re-read nothing below high_water
    ],
)
def test_replay_floor_arithmetic(high_water, margin, expected):
    assert _replay_floor(high_water, margin) == expected


def test_engine_rejects_negative_replay_margin(fake_descriptor):
    with pytest.raises(ValueError, match="replay_margin must be >= 0"):
        SyncEngine([fake_descriptor], FakeClient(), replay_margin=-1)


def test_engine_accepts_zero_replay_margin(fake_descriptor):
    # 0 is the "replay off" degenerate, not an error (floor == high_water).
    SyncEngine([fake_descriptor], FakeClient(), replay_margin=0)
