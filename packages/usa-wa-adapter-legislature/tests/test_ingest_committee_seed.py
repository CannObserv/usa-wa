"""Tests for ingest_committee_seed.py — no-WSL seed materialization (#39)."""

from __future__ import annotations

import hashlib
import os
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from clearinghouse_core.provenance import FetchEvent, RawPayload
from clearinghouse_core.seed_manifest import SeedIntegrityError, write_sidecars
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature import ingest_committee_seed as ingest_module
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.committee_seed import SeedCommittee, serialize_seed
from usa_wa_adapter_legislature.ingest_committee_seed import (
    SEED_RESOURCE_ID,
    ingest_committee_seed,
)


def _write_seed(tmp_path, committees, *, bienniums=("2023-24", "2025-26")):
    content = serialize_seed(committees, bienniums=list(bienniums))
    seed_path = tmp_path / "joint_other_committees_seed.json"
    seed_path.write_bytes(content)
    write_sidecars(seed_path, content, extra={"bienniums": list(bienniums)})
    return seed_path, content


async def test_ingest_materializes_cohort_with_synthetic_provenance(db_session, usa_wa, tmp_path):
    """A verified seed inserts org_type='other' rows under the legislature anchor, and
    records a synthetic FetchEvent (content_hash) + archived RawPayload."""
    seed_path, content = _write_seed(
        tmp_path,
        [
            SeedCommittee("-140", "Joint Joint Transportation Committee", "JTC", "JTC", None),
            SeedCommittee("-12", "Other LEAP", "LEAP", "LEAP", None),
        ],
    )

    summary = await ingest_committee_seed(db_session, seed_path=seed_path)
    assert summary.in_seed == 2
    assert summary.inserted == 2
    assert summary.provenance_recorded is True

    rows = {
        o.source_id: o
        for o in (
            await db_session.execute(select(Organization).where(Organization.org_type == "other"))
        )
        .scalars()
        .all()
    }
    assert set(rows) == {"-140", "-12"}
    # Parented to the legislature anchor (resolved via the current biennium bootstrap).
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium="2025-26", jurisdiction_id=usa_wa.id
    )
    assert rows["-140"].parent_organization_id == anchors.legislature_id
    assert rows["-140"].name == "Joint Joint Transportation Committee"

    # Synthetic provenance: FetchEvent hashed over the seed bytes + RawPayload archived.
    event = (
        await db_session.execute(
            select(FetchEvent).where(FetchEvent.resource_id == SEED_RESOURCE_ID)
        )
    ).scalar_one()
    assert event.content_hash == hashlib.sha256(content).digest()
    raw = (
        await db_session.execute(select(RawPayload).where(RawPayload.fetch_event_id == event.id))
    ).scalar_one()
    assert raw.body == content


async def test_ingest_is_fill_only_leaving_existing_rows_untouched(db_session, usa_wa, tmp_path):
    """A body the DB already holds (e.g. a newer name from the daily refresh) is not
    overwritten — the seed is a floor, not an authority."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium="2025-26", jurisdiction_id=usa_wa.id
    )
    await db_session.execute(
        pg_insert(Organization).values(
            source="usa_wa_legislature",
            source_id="-140",
            jurisdiction_id=usa_wa.id,
            name="Joint Joint Transportation Committee (renamed)",
            org_type="other",
            parent_organization_id=anchors.legislature_id,
        )
    )
    seed_path, _ = _write_seed(
        tmp_path,
        [SeedCommittee("-140", "Joint Joint Transportation Committee", "JTC", "JTC", None)],
    )

    summary = await ingest_committee_seed(db_session, seed_path=seed_path)
    assert summary.in_seed == 1
    assert summary.inserted == 0  # conflict → skipped

    org = (
        await db_session.execute(select(Organization).where(Organization.source_id == "-140"))
    ).scalar_one()
    assert org.name == "Joint Joint Transportation Committee (renamed)"  # preserved


async def test_reingesting_same_seed_skips_duplicate_provenance(db_session, usa_wa, tmp_path):
    """Re-ingesting byte-identical seed records no new FetchEvent/RawPayload (append-only
    dedup); the fill-only org upsert still runs and is idempotent."""
    seed_path, _ = _write_seed(
        tmp_path,
        [SeedCommittee("-140", "Joint Joint Transportation Committee", "JTC", "JTC", None)],
    )
    first = await ingest_committee_seed(db_session, seed_path=seed_path)
    assert first.provenance_recorded is True

    second = await ingest_committee_seed(db_session, seed_path=seed_path)
    assert second.provenance_recorded is False
    assert second.inserted == 0  # org already present

    events = (
        (
            await db_session.execute(
                select(FetchEvent).where(FetchEvent.resource_id == SEED_RESOURCE_ID)
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1  # only the first ingest recorded provenance


async def test_ingest_fails_closed_on_tampered_seed(db_session, usa_wa, tmp_path):
    """Bytes that diverge from the sidecar digest raise rather than ingest."""
    seed_path, _ = _write_seed(
        tmp_path,
        [SeedCommittee("-140", "Joint Joint Transportation Committee", "JTC", "JTC", None)],
    )
    seed_path.write_bytes(seed_path.read_bytes() + b"\n# tamper\n")  # sidecar now stale

    with pytest.raises(SeedIntegrityError):
        await ingest_committee_seed(db_session, seed_path=seed_path)


async def test_main_returns_2_when_database_url_unset(monkeypatch, capsys):
    """Missing DATABASE_URL → stderr message + exit 2 (config error)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(ingest_module, "configure_logging"):
        code = await ingest_module._main([])
    assert code == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


async def test_main_returns_1_when_ingest_raises(monkeypatch, capsys, test_engine):
    """An exception from the ingest is caught, logged, and produces exit 1."""
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])

    async def boom(*_args, **_kwargs):
        raise RuntimeError("simulated ingest failure")

    with (
        patch.object(ingest_module, "configure_logging"),
        patch.object(ingest_module, "ingest_committee_seed", boom),
        patch.object(ingest_module.logger, "exception") as mock_exception,
    ):
        code = await ingest_module._main([])
    assert code == 1
    mock_exception.assert_called_once_with("wsl_committee_seed_ingest_failed")
