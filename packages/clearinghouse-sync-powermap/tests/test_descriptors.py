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


@pytest.mark.parametrize(
    ("identifiers", "expected"),
    [
        ([{"type_slug": "org_wa_legislature_committee_id", "value": "875"}], True),
        ([{"type_slug": "org_wa_legislature_committee_id", "value": "anything-else"}], True),
        ([{"type_slug": "org_wa_pdc", "value": "875"}], False),  # different type
        ([], False),  # present but empty
        (None, False),  # identifiers key absent
    ],
)
def test_record_has_identifier_type_is_value_agnostic(identifiers, expected):
    """``record_has_identifier_type`` matches ANY identifier of the type regardless of
    value (the org name-match re-key guard's claim check), unlike
    ``record_has_identifier`` which is (type, value)-exact."""
    record = {} if identifiers is None else {"identifiers": identifiers}
    assert (
        EntityDescriptor.record_has_identifier_type(record, "org_wa_legislature_committee_id")
        is expected
    )


def test_record_has_identifier_type_vs_value_exact():
    """Contrast the two helpers: a candidate carrying our type under a *different*
    value is claimed (type-match True) but not a (type, value)-exact match."""
    record = {"identifiers": [{"type_slug": "org_wa_legislature_committee_id", "value": "875"}]}
    assert EntityDescriptor.record_has_identifier_type(record, "org_wa_legislature_committee_id")
    assert not EntityDescriptor.record_has_identifier(
        record, "org_wa_legislature_committee_id", "17366"
    )


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


# --- LWW no-op gate template (usa-wa#109) ----------------------------------


class _GatedDescriptor(FakeDescriptor):
    """Opts into the gate and declares a comparator over the observation's ``name``."""

    local_newer_noop_gate = True

    def observation_matches_record(self, observation: dict, record: dict) -> bool:
        return observation.get("name") == record.get("name")


class _DepsNotReadyDescriptor(_GatedDescriptor):
    """Gated, but its PM prerequisites are unmet."""

    async def dependencies_ready(self, session, row) -> bool:
        return False


class _ExplodingObservationDescriptor(FakeDescriptor):
    """Un-gated; raises if the template ever builds its observation."""

    async def to_observation(self, session, row) -> dict:
        raise AssertionError("to_observation must not be called for an un-gated descriptor")


async def test_local_newer_is_noop_default_is_opt_in_and_builds_nothing():
    """The gate stays opt-in (usa-wa#102's contract): a descriptor that hasn't set
    ``local_newer_noop_gate`` returns False **without** building an observation, so
    non-participating cohorts pay no DB cost on the local-newer path."""
    row = FakeEntity(source="s", source_id="X-1", name="Widget", pm_fake_id=ULID())

    assert await _ExplodingObservationDescriptor().local_newer_is_noop(None, row, {}) is False


async def test_local_newer_is_noop_template_compares_when_gated():
    """Gated + comparator: the template builds the observation and delegates the verdict."""
    row = FakeEntity(source="s", source_id="X-1", name="Widget", pm_fake_id=ULID())
    descriptor = _GatedDescriptor()

    assert await descriptor.local_newer_is_noop(None, row, {"name": "Widget"}) is True
    assert await descriptor.local_newer_is_noop(None, row, {"name": "Gadget"}) is False


async def test_local_newer_is_noop_template_guards_on_dependencies():
    """The deps guard is structural (usa-wa#109), not per-descriptor: a gated row whose PM
    prerequisites are unmet is never a no-op, even when the comparator would say otherwise.

    Without this a deps-not-ready row would build a garbage observation (``"None"`` anchors)
    that could compare equal by accident and adopt PM's clock — silently erasing a pending
    change rather than deferring it."""
    row = FakeEntity(source="s", source_id="X-1", name="Widget", pm_fake_id=ULID())

    assert (
        await _DepsNotReadyDescriptor().local_newer_is_noop(None, row, {"name": "Widget"}) is False
    )


async def test_gated_descriptor_without_comparator_never_noops():
    """Flipping the flag but forgetting the comparator degrades to the pre-gate behaviour
    (base ``observation_matches_record`` → False), never a blind clock-adopt."""

    class _FlagOnly(FakeDescriptor):
        local_newer_noop_gate = True

    row = FakeEntity(source="s", source_id="X-1", name="Widget", pm_fake_id=ULID())

    assert await _FlagOnly().local_newer_is_noop(None, row, {"name": "Widget"}) is False


async def test_set_last_updated_survives_flush_when_value_is_unchanged(db_session):
    """usa-wa#109: stamping the clock with the value it *already* holds must still land.

    ``updated_at`` carries an ``onupdate`` callable, and SQLAlchemy applies it to any
    UPDATE whose SET clause omits the column. Assigning a value equal to the loaded one
    registers **no net attribute change**, so without ``flag_modified`` the column drops
    out of the SET clause and the onupdate silently overwrites the stamp with ``now()``.

    That no-change write is precisely the "preserve this clock" case the anchor-stamp
    preserve depends on, so this is the guard for it: a plain equality assertion right
    after the assignment would pass even with the bug — the divergence only appears
    after the flush.
    """
    settled = datetime(2020, 1, 1, tzinfo=UTC)
    row = FakeEntity(source="wsl", source_id="clock-1", name="Widget", updated_at=settled)
    db_session.add(row)
    await db_session.flush()

    descriptor = FakeDescriptor()
    row.name = "Widget edited"  # dirty the row so a real UPDATE is emitted
    descriptor.set_last_updated(row, settled)  # ...restamping the SAME clock
    await db_session.flush()

    assert row.updated_at == settled  # not clobbered by onupdate's now()
