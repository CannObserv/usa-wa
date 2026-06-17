"""EntityDescriptor contract + observation value-type tests (engine step 2)."""

from datetime import UTC, datetime

import pytest
from ulid import ULID

from clearinghouse_sync_powermap.client import ObservationResult
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, normalize_name
from clearinghouse_sync_powermap.models import (
    DISPOSITION_AUTO_ATTACHED,
    DISPOSITION_NEW,
    DISPOSITION_REJECTED,
)
from clearinghouse_sync_powermap.testing import FakeDescriptor, FakeEntity


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Consumer Protection & Business Committee", "Consumer Protection and Business Committee"),
        ("Ways and Means", "ways  and   means"),
        ("WA House of Representatives", "wa house of representatives!"),
        # Unaccent — mirrors PM's pm_unaccent_simple FTS config (#201).
        ("José García", "Jose Garcia"),
        ("Renée Núñez", "renee nunez"),
    ],
)
def test_normalize_name_folds_variants_equal(a, b):
    assert normalize_name(a) == normalize_name(b)


def test_normalize_name_unaccent_preserves_letters():
    """Accented letters fold to their base (not shredded into separators)."""
    assert normalize_name("José") == "jose"


def test_normalize_name_distinguishes_real_differences():
    assert normalize_name("Senate Ways and Means") != normalize_name("House Ways and Means")


def test_anchor_get_set(fake_descriptor):
    """``anchor_value`` / ``set_anchor`` read+write the configured column."""
    row = FakeEntity(source="s", source_id="1", name="x")
    assert fake_descriptor.anchor_value(row) is None
    pm_id = ULID()
    fake_descriptor.set_anchor(row, pm_id)
    assert fake_descriptor.anchor_value(row) == pm_id


def test_natural_key_values(fake_descriptor):
    """``natural_key_values`` extracts the tuple in declared order."""

    row = FakeEntity(source="wsl", source_id="42", name="x")
    assert fake_descriptor.natural_key_values(row) == ("wsl", "42")


async def test_to_observation_shape(db_session, fake_descriptor):
    row = FakeEntity(source="wsl", source_id="42", name="Widget")
    assert await fake_descriptor.to_observation(db_session, row) == {
        "source": "wsl",
        "source_id": "42",
        "name": "Widget",
    }


def test_last_updated_handles_row_and_record(fake_descriptor):
    """The comparator reads local ``updated_at`` and a PM record's field alike."""

    row = FakeEntity(source="s", source_id="1", name="x")
    row.updated_at = datetime(2026, 6, 1, tzinfo=UTC)
    assert fake_descriptor.last_updated(row) == datetime(2026, 6, 1, tzinfo=UTC)

    record = {"updated_at": "2026-06-02T00:00:00Z"}
    assert fake_descriptor.last_updated(record) == datetime(2026, 6, 2, tzinfo=UTC)


def test_observation_result_anchored():
    pm_id = ULID()
    assert ObservationResult(DISPOSITION_NEW, pm_id, {}).anchored
    assert ObservationResult(DISPOSITION_AUTO_ATTACHED, pm_id, {}).anchored
    # Anchoring disposition but no id → not anchored.
    assert not ObservationResult(DISPOSITION_NEW, None, {}).anchored


def test_observation_result_rejected():
    result = ObservationResult(DISPOSITION_REJECTED, None, {"error": "dupe"})
    assert result.rejected
    assert not result.anchored


# --- reconcile_mode contract (usa-wa#13) -------------------------------------


def test_reconcile_mode_defaults_to_none():
    """The base contract defaults to the ``none`` reconcile mode (no backstop) —
    a descriptor opts into a backstop explicitly."""
    assert EntityDescriptor.reconcile_mode == "none"


def test_reconcile_enabled_is_derived_from_mode():
    """Back-compat: ``reconcile_enabled`` is True iff a reconcile backstop runs
    (any non-``none`` mode)."""

    class NoneMode(FakeDescriptor):
        reconcile_mode = "none"

    class FullList(FakeDescriptor):
        reconcile_mode = "full_list"

    class Cohort(FakeDescriptor):
        reconcile_mode = "anchored_cohort"

    assert NoneMode().reconcile_enabled is False
    assert FullList().reconcile_enabled is True
    assert Cohort().reconcile_enabled is True
