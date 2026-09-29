"""Roster-PDF raw-tier harvest (#421): one edition into the file store the #302 pipeline reads."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

import httpx
import pytest
import respx

from clearinghouse_core.job import EXIT_DEGRADED
from clearinghouse_core.rawstore import RAW_ROOT_ENV, RawStore
from clearinghouse_core.testing import patch_job_runtime
from usa_wa_adapter_legislature.roster_pdf import edition as edition_module
from usa_wa_adapter_legislature.roster_pdf import raw_harvest as raw_harvest_module
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.raw_harvest import (
    RosterRawHarvestSummary,
    harvest_roster_raw,
)
from usa_wa_adapter_legislature.roster_pdf.resources import ROSTER_RESOURCE_PREFIX
from usa_wa_adapter_legislature.roster_pdf.transport import DEFAULT_INDEX_URL, DEFAULT_ROSTER_URL

REVISION = "2025-06-05"
NEW_EDITION = "2027-06-01"


def _store(root) -> RawStore:
    return RawStore(root, ROSTER_SOURCE_SLUG)


def _newest_roster_id(store: RawStore) -> str:
    """The resource the #302 roster staging model parses: newest ``legroster:`` by fetch."""
    candidates = {
        rid: entry
        for rid, entry in store.latest().items()
        if rid.startswith(ROSTER_RESOURCE_PREFIX)
    }
    return max(candidates, key=lambda rid: candidates[rid]["fetched_at"])


def _stamped(revision: str):
    """Make the fixture PDF read as another edition: the stamp is the only thing checked."""
    return patch.object(edition_module, "extract_revision_date", return_value=revision)


@pytest.fixture
def roster_route(roster_pdf_bytes):
    with respx.mock:
        yield respx.get(DEFAULT_ROSTER_URL).mock(
            return_value=httpx.Response(
                200, content=roster_pdf_bytes, headers={"content-type": "application/pdf"}
            )
        )


class TestHarvest:
    async def test_the_edition_lands_in_the_raw_store(
        self, tmp_path, roster_route, roster_pdf_bytes
    ) -> None:
        """The acceptance: an archived edition is what roster staging parses, with no export."""
        summary = await harvest_roster_raw(tmp_path, revision=REVISION)

        store = _store(tmp_path)
        assert _newest_roster_id(store) == f"legroster:{REVISION}"
        entry = store.latest()[f"legroster:{REVISION}"]
        assert entry["sha256"] == hashlib.sha256(roster_pdf_bytes).hexdigest()
        assert store.object_path(entry["sha256"]).read_bytes() == roster_pdf_bytes
        assert (summary.fetched, summary.unchanged, summary.skipped_fresh) == (1, 0, 0)

    async def test_the_manifest_cites_where_the_bytes_came_from(self, tmp_path, roster_route):
        await harvest_roster_raw(tmp_path, revision=REVISION)

        [manifest_path] = _store(tmp_path).manifest_paths()
        [entry] = json.loads(manifest_path.read_text())["entries"]
        assert entry["url"] == DEFAULT_ROSTER_URL
        assert entry["status"] == "ok"
        assert entry["content_type"] == "application/pdf"

    async def test_the_journal_names_the_absolute_store_it_landed_in(
        self, tmp_path, roster_route, caplog, monkeypatch
    ) -> None:
        """The default root is ``raw/`` under the cwd: run from a worktree, the edition lands
        where the pipeline never looks and the run still exits 0. The completion line names
        the store, so a stray landing is visible (CR 1)."""
        monkeypatch.chdir(tmp_path)
        with caplog.at_level("INFO"):
            await harvest_roster_raw("raw", revision=REVISION)

        [record] = [r for r in caplog.records if r.getMessage() == "roster_raw_harvest_complete"]
        assert record.store == str(tmp_path / "raw" / ROSTER_SOURCE_SLUG)

    async def test_a_rerun_inside_the_freshness_window_does_not_fetch(
        self, tmp_path, roster_route
    ) -> None:
        """The document changes ~biennially; re-running must not re-download 5.7MB."""
        await harvest_roster_raw(tmp_path, revision=REVISION)
        summary = await harvest_roster_raw(tmp_path, revision=REVISION)

        assert roster_route.call_count == 1
        assert (summary.fetched, summary.skipped_fresh) == (0, 1)

    async def test_force_refetches_and_dedups_identical_bytes(self, tmp_path, roster_route) -> None:
        await harvest_roster_raw(tmp_path, revision=REVISION)
        before = _store(tmp_path).latest()[f"legroster:{REVISION}"]["fetched_at"]

        summary = await harvest_roster_raw(tmp_path, revision=REVISION, force=True)

        assert roster_route.call_count == 2
        assert (summary.fetched, summary.unchanged) == (1, 1)
        assert _store(tmp_path).latest()[f"legroster:{REVISION}"]["fetched_at"] > before


class TestOperatorConditions:
    """A new edition and an unlocatable document both need an operator, so both return a
    summary the job maps to exit 4, and neither writes anything."""

    async def test_a_new_edition_refuses_the_old_revision(self, tmp_path, roster_route) -> None:
        with _stamped(NEW_EDITION):
            summary = await harvest_roster_raw(tmp_path, revision=REVISION)

        assert summary.mismatch is not None
        assert f"--revision {NEW_EDITION}" in summary.mismatch
        assert summary.fetched == 0
        assert not any(tmp_path.iterdir())

    @respx.mock
    async def test_an_unlocatable_document_is_unavailable_not_a_crash(self, tmp_path) -> None:
        respx.get(DEFAULT_ROSTER_URL).mock(return_value=httpx.Response(404))
        respx.get(DEFAULT_INDEX_URL).mock(
            return_value=httpx.Response(200, html="<a href='/media/x/budget.pdf'>B</a>")
        )
        summary = await harvest_roster_raw(tmp_path, revision=REVISION)

        assert summary.unavailable is True
        assert not any(tmp_path.iterdir())

    @respx.mock
    async def test_an_outage_raises_and_writes_nothing(self, tmp_path) -> None:
        """Exit 1, not 4: the runbook reads 1 as "an outage, the next run retries"."""
        respx.get(DEFAULT_ROSTER_URL).mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            await harvest_roster_raw(tmp_path, revision=REVISION)
        assert not any(tmp_path.iterdir())

    async def test_an_unreadable_stamp_refuses_to_archive(self, tmp_path, roster_route) -> None:
        """The stale-edition guard is the stamp check, so it only holds when the stamp reads. A
        new edition whose front matter changed layout, forced under the old ``--revision``,
        would otherwise land new bytes under the old key as the newest edition (CR 4)."""
        with _stamped(None):
            summary = await harvest_roster_raw(tmp_path, revision=REVISION, force=True)

        assert summary.unreadable is True
        assert summary.fetched == 0
        assert not any(tmp_path.iterdir())

    async def test_a_dry_run_with_an_unreadable_stamp_only_warns(
        self, tmp_path, roster_route
    ) -> None:
        """Scope of CR 4: refusing is about what gets *archived*; a dry run archives nothing."""
        with _stamped(None):
            summary = await harvest_roster_raw(tmp_path, revision=REVISION, dry_run=True)

        assert summary.unreadable is False
        assert summary.fetched == 1

    async def test_a_stale_revision_cannot_overtake_a_newer_edition(
        self, tmp_path, roster_route
    ) -> None:
        """Staging parses the newest ``legroster:`` by fetch time, not by revision. A forced
        re-run under the old ``--revision`` must therefore be refused, or the published
        datasets would silently roll back to the older edition."""
        with _stamped(NEW_EDITION):
            await harvest_roster_raw(tmp_path, revision=NEW_EDITION)
            summary = await harvest_roster_raw(tmp_path, revision=REVISION, force=True)

        assert summary.mismatch is not None
        assert _newest_roster_id(_store(tmp_path)) == f"legroster:{NEW_EDITION}"


class TestDryRun:
    """The monthly edition re-check (#237) runs ``--dry-run --force``."""

    async def test_a_dry_run_fetches_and_verifies_but_writes_nothing(
        self, tmp_path, roster_route
    ) -> None:
        """The raw store has no transaction to roll back, so a dry run must never open one."""
        summary = await harvest_roster_raw(tmp_path, revision=REVISION, dry_run=True)

        assert roster_route.call_count == 1
        assert summary.dry_run is True
        assert summary.fetched == 1
        assert not any(tmp_path.iterdir())

    async def test_a_forced_recheck_sees_a_new_edition_past_a_fresh_archive(
        self, tmp_path, roster_route
    ) -> None:
        await harvest_roster_raw(tmp_path, revision=REVISION)
        with _stamped(NEW_EDITION):
            summary = await harvest_roster_raw(
                tmp_path, revision=REVISION, dry_run=True, force=True
            )

        assert roster_route.call_count == 2
        assert summary.mismatch is not None and f"--revision {NEW_EDITION}" in summary.mismatch

    async def test_an_unforced_recheck_is_blind_inside_the_freshness_window(
        self, tmp_path, roster_route
    ) -> None:
        """The trap ``--force`` avoids: a new edition is published, and the run reports clean."""
        await harvest_roster_raw(tmp_path, revision=REVISION)
        with _stamped(NEW_EDITION):
            summary = await harvest_roster_raw(tmp_path, revision=REVISION, dry_run=True)

        assert roster_route.call_count == 1
        assert summary.mismatch is None


class TestCli:
    def test_the_root_flag_names_the_store(self, monkeypatch, tmp_path) -> None:
        patch_job_runtime(monkeypatch)
        calls: list = []

        async def _fake(root, **kwargs):
            calls.append(root)
            return RosterRawHarvestSummary(revision=kwargs["revision"])

        with patch.object(raw_harvest_module, "harvest_roster_raw", _fake):
            assert raw_harvest_module.main(["--root", str(tmp_path)]) == 0
        assert [str(r) for r in calls] == [str(tmp_path)]

    def test_without_root_the_store_is_usa_wa_raw_root(self, monkeypatch, tmp_path) -> None:
        """The recheck unit and the runbook pass no ``--root``: the prod store comes from
        ``USA_WA_RAW_ROOT`` in ``/etc/usa-wa/.env`` (CR 3)."""
        patch_job_runtime(monkeypatch)
        monkeypatch.setenv(RAW_ROOT_ENV, str(tmp_path))
        calls: list = []

        async def _fake(root, **kwargs):
            calls.append(root)
            return RosterRawHarvestSummary(revision=kwargs["revision"])

        with patch.object(raw_harvest_module, "harvest_roster_raw", _fake):
            assert raw_harvest_module.main([]) == 0
        assert [str(r) for r in calls] == [str(tmp_path)]

    def test_pause_seconds_overrides_the_host_limiter_only_when_passed(self, monkeypatch):
        """An unflagged run leaves the env-seeded ``leg.wa.gov`` interval in force (#236)."""
        patch_job_runtime(monkeypatch)

        async def _fake(root, **kwargs):
            return RosterRawHarvestSummary(revision=kwargs["revision"])

        with (
            patch.object(raw_harvest_module, "harvest_roster_raw", _fake),
            patch.object(raw_harvest_module, "configure_leg_rate_limit") as configure,
        ):
            assert raw_harvest_module.main(["--dry-run"]) == 0
            assert configure.call_count == 0

            assert raw_harvest_module.main(["--dry-run", "--pause-seconds", "3.5"]) == 0
            configure.assert_called_once_with(3.5)

    @pytest.mark.parametrize(
        "condition",
        [{"mismatch": "stamps 2027-06-01"}, {"unavailable": True}, {"unreadable": True}],
        ids=["new-edition", "unlocatable", "unreadable-stamp"],
    )
    def test_operator_conditions_exit_degraded(self, monkeypatch, condition) -> None:
        patch_job_runtime(monkeypatch)

        async def _degraded(root, **kwargs):
            return RosterRawHarvestSummary(revision=kwargs["revision"], **condition)

        with patch.object(raw_harvest_module, "harvest_roster_raw", _degraded):
            assert raw_harvest_module.main([]) == EXIT_DEGRADED
