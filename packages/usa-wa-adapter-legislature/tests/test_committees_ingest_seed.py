"""Tests for committees/ingest_seed.py — no-WSL seed materialization (#39)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from clearinghouse_core import job as job_module
from clearinghouse_core.provenance import FetchEvent, RawPayload
from clearinghouse_core.seed_manifest import SeedIntegrityError, write_sidecars
from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.committees import ingest_seed as ingest_module
from usa_wa_adapter_legislature.committees.ingest_seed import (
    SEED_RESOURCE_ID,
    IngestSummary,
    ingest_seed,
)
from usa_wa_adapter_legislature.committees.seed import SeedCommittee, serialize_seed


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

    summary = await ingest_seed(db_session, seed_path=seed_path)
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

    summary = await ingest_seed(db_session, seed_path=seed_path)
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
    first = await ingest_seed(db_session, seed_path=seed_path)
    assert first.provenance_recorded is True

    second = await ingest_seed(db_session, seed_path=seed_path)
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
        await ingest_seed(db_session, seed_path=seed_path)


# --- CLI (#179b: the shared job harness) --------------------------------------


def test_main_returns_2_when_database_url_unset(monkeypatch, capsys):
    """Unchanged: missing DATABASE_URL → stderr message + exit 2 (config error)."""

    def _raise(_role="app"):
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(job_module, "get_database_url", _raise)
    assert ingest_module.main([]) == 2
    assert "DATABASE_URL is not set" in capsys.readouterr().err


def test_main_returns_1_when_ingest_raises(monkeypatch):
    """Unchanged: an exception from the ingest is caught, logged, and exits 1 — the
    harness's ``job_failed`` record replaces the per-CLI ``logger.exception`` call."""
    patch_job_runtime(monkeypatch)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("simulated ingest failure")

    with patch.object(ingest_module, "ingest_seed", boom):
        assert ingest_module.main([]) == 1


def test_main_dry_run_now_rolls_back(monkeypatch, capsys):
    """New capability, not a changed contract: the seed ingest never had a --dry-run;
    the harness gives every job one, and it rolls the ingest back."""
    recording = patch_job_runtime(monkeypatch)

    async def _fake_ingest(_session, **_kwargs):
        return IngestSummary(
            in_seed=4, inserted=2, provenance_recorded=True, seed_path=Path("seed.json")
        )

    with patch.object(ingest_module, "ingest_seed", _fake_ingest):
        code = ingest_module.main(["--dry-run", "--json"])

    assert code == 0
    assert (recording.committed, recording.rolled_back) == (0, 1)
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["job"] == ingest_module.JOB_SLUG
    assert payload["counters"]["in_seed"] == 4
    assert payload["counters"]["inserted"] == 2
