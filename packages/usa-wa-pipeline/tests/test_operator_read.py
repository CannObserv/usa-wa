"""The curated operator-event read seam (#309).

Operator events are the span transform's one non-raw-store input — human
succession decisions. Like the crosswalk seam, a db-free build must be an
explicit choice, never the silent consequence of a missing env var.
"""

import pytest

from clearinghouse_core.config import get_settings
from usa_wa_pipeline.operator_read import EventRow, operator_events


def test_events_are_empty_only_under_the_hermetic_marker(monkeypatch) -> None:
    monkeypatch.setenv("USA_WA_PIPELINE_HERMETIC", "1")
    assert operator_events() == []

    monkeypatch.delenv("USA_WA_PIPELINE_HERMETIC", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            operator_events()
    finally:
        get_settings.cache_clear()


def test_event_row_carries_the_overlay_attribute_surface() -> None:
    """`operator_overlay.from_rows` reads these five attributes by name."""
    row = EventRow(
        member_id="100",
        kind="departed",
        effective_date=None,
        seat_kind=None,
        seat_discriminator=None,
    )
    for attr in ("member_id", "kind", "effective_date", "seat_kind", "seat_discriminator"):
        assert hasattr(row, attr)
