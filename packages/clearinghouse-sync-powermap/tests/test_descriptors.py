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


# --- archival mirror (PM archived_at → local archived_at, usa-wa#41/#42) ---


def test_mirror_archival_stamps_archived_at_from_pm(fake_descriptor):
    """A PM record carrying ``archived_at`` stamps the local ``archived_column``
    with PM's own clock (set-or-clear, mirrors LWW)."""
    row = FakeEntity(source="s", source_id="1", name="x")
    fake_descriptor.mirror_archival(row, {"archived_at": "2026-06-20T00:00:00Z"})
    assert row.archived_at == datetime(2026, 6, 20, tzinfo=UTC)


def test_mirror_archival_clears_archived_at_when_unarchived(fake_descriptor):
    """``archived_at`` back to null/absent clears the local mirror (PM un-archive)."""
    row = FakeEntity(source="s", source_id="1", name="x")
    row.archived_at = datetime(2026, 6, 20, tzinfo=UTC)
    fake_descriptor.mirror_archival(row, {})
    assert row.archived_at is None


def test_mirror_archival_does_not_touch_deleted_at(fake_descriptor):
    """Archival is the reversible axis — mirroring it never sets the terminal
    ``deleted_at`` tombstone (the two axes are independent, usa-wa#42)."""
    row = FakeEntity(source="s", source_id="1", name="x")
    fake_descriptor.mirror_archival(row, {"archived_at": "2026-06-20T00:00:00Z"})
    assert row.deleted_at is None


def test_mirror_archival_noop_without_archived_column():
    """A descriptor that doesn't mirror archival (``archived_column is None``) is a
    no-op — so non-archival-mirroring entities (e.g. jurisdictions) are unaffected
    even if PM sends ``archived_at``."""

    class _NoArchival(FakeDescriptor):
        archived_column = None

    row = FakeEntity(source="s", source_id="1", name="x")
    _NoArchival().mirror_archival(row, {"archived_at": "2026-06-20T00:00:00Z"})
    assert row.archived_at is None  # untouched


# --- enrich-on-match carry-through (base contract; lives where the loop does) ---


class _EnrichDescriptor(FakeDescriptor):
    """FakeDescriptor variant exercising :meth:`to_enrich_observation`.

    Its ``to_observation`` returns the identifier plus a declared carry field
    (``extra``) and an *undeclared* one (``omitted``) so a single call proves the
    loop copies only what ``enrich_carry_fields`` names.
    """

    enrich_identifier_type = "pm_fake_anchor"
    enrich_carry_fields = ("names", "extra")

    async def to_observation(self, session: object, row: object) -> dict:
        """Base payload with a declared and an undeclared carry field."""
        return {
            "identifier_type": "fake_source_id",
            "identifier_value": row.source_id,
            "names": [{"name": row.name, "name_type": "legal"}],
            "extra": ["carried"],
            "omitted": ["dropped"],
        }


def test_enrich_carry_fields_defaults_to_names_only():
    """The base default is typed-name evidence only — siblings opt into more."""
    assert EntityDescriptor.enrich_carry_fields == ("names",)


async def test_to_enrich_observation_carries_declared_fields_only():
    """Re-keys to the PM anchor, demotes the real id, carries declared fields."""
    desc = _EnrichDescriptor()
    anchor = ULID()
    row = FakeEntity(source="s", source_id="X-1", name="Widget", pm_fake_id=anchor)

    obs = await desc.to_enrich_observation(None, row)

    assert obs["identifier_type"] == "pm_fake_anchor"
    assert obs["identifier_value"] == str(anchor)
    assert obs["additional_identifiers"] == [
        {"identifier_type_slug": "fake_source_id", "identifier_value": "X-1"}
    ]
    # Declared carry fields ride along; an undeclared base field does not.
    assert obs["names"] == [{"name": "Widget", "name_type": "legal"}]
    assert obs["extra"] == ["carried"]
    assert "omitted" not in obs


async def test_to_enrich_observation_honours_narrowed_carry_set():
    """Default carry set (``names`` only) drops a sibling's extra fields."""

    class _NamesOnly(_EnrichDescriptor):
        enrich_carry_fields = ("names",)

    row = FakeEntity(source="s", source_id="X-2", name="Gadget", pm_fake_id=ULID())

    obs = await _NamesOnly().to_enrich_observation(None, row)

    # Re-keying still holds on the narrowed path; only the extra field drops.
    assert obs["identifier_type"] == "pm_fake_anchor"
    assert obs["additional_identifiers"] == [
        {"identifier_type_slug": "fake_source_id", "identifier_value": "X-2"}
    ]
    assert obs["names"] == [{"name": "Gadget", "name_type": "legal"}]
    assert "extra" not in obs
