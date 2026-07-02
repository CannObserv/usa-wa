"""Full rename-chain emit (sub-project 3, Phase B, step 8).

Wires the archive-first roster provider → chain builder → per-transition dated-name
emission (reusing the #46/#56 spine's _emit_names guards). Tests the emit loop +
absent-id classification + empty-archive abort against a fake provider + fake PM.
"""

from __future__ import annotations

import json

from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import DeliveryBlockedError, ObservationResult
from clearinghouse_sync_powermap.models import DISPOSITION_AUTO_ATTACHED
from usa_wa_sync_powermap import reconcile_committee_name_chain as cli
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor


class _FakeProvider:
    def __init__(self, cohorts):
        self._cohorts = cohorts

    async def archived_bienniums(self):
        return sorted(self._cohorts)

    async def cohort(self, biennium):
        return self._cohorts[biennium]


class _FakePM:
    def __init__(self):
        self.posted = []

    async def post_observation(self, path, payload):
        self.posted.append(payload)
        return ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=ULID(), raw={})

    async def aclose(self):
        pass


async def _add_committee(db_session, usa_wa, *, source_id, anchor, name):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=name,
        org_type="committee",
        pm_organization_id=anchor,
        jurisdiction_id=usa_wa.id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def test_emits_chain_and_classifies_absent(db_session, usa_wa):
    # id 1 renamed + produced+anchored → emitted; id 3 renamed but never produced → unproduced.
    await _add_committee(db_session, usa_wa, source_id="1", anchor=ULID(), name="whatever")
    provider = _FakeProvider(
        {
            "2021-22": {"1": "House Committee on Transportation", "3": "Old Ghost"},
            "2023-24": {"1": "House Transportation Committee", "3": "New Ghost"},
        }
    )
    pm = _FakePM()
    summary = await cli.emit_rename_chain(db_session, OrganizationDescriptor(), pm, provider)

    assert summary["transitions"] == 2
    assert summary["emitted"] == 1
    assert summary["skipped_unproduced"] == 1
    assert summary["aborted"] is None
    # the emitted observation carries the former/legal windows
    assert len(pm.posted) == 1
    types = {n["name_type"] for n in pm.posted[0]["names"]}
    assert types == {"former", "legal"}


async def test_dry_run_posts_nothing(db_session, usa_wa):
    await _add_committee(db_session, usa_wa, source_id="1", anchor=ULID(), name="x")
    provider = _FakeProvider({"2021-22": {"1": "Name A"}, "2023-24": {"1": "Name B"}})
    pm = _FakePM()
    summary = await cli.emit_rename_chain(
        db_session, OrganizationDescriptor(), pm, provider, dry_run=True
    )
    assert summary["transitions"] == 1
    assert summary["emitted"] == 0
    assert pm.posted == []


async def test_empty_archive_aborts(db_session, usa_wa):
    provider = _FakeProvider({})
    summary = await cli.emit_rename_chain(db_session, OrganizationDescriptor(), _FakePM(), provider)
    assert summary["aborted"] == "empty_archive"


async def test_storm_boundary_reported(db_session, usa_wa):
    prior = {str(i): f"Name {i}" for i in range(8)}
    reformatted = {str(i): f"X: Name {i}" for i in range(8)}
    provider = _FakeProvider({"2021-22": prior, "2023-24": reformatted})
    summary = await cli.emit_rename_chain(db_session, OrganizationDescriptor(), _FakePM(), provider)
    assert summary["transitions"] == 0
    assert summary["storm_skipped"] and summary["storm_skipped"][0]["biennium"] == "2023-24"


# --- main exit codes ----------------------------------------------------------


def test_main_clean_exits_zero(monkeypatch, capsys):
    async def _fake(_args):
        return {"emitted": 3, "rejected": 0, "failed": 0, "aborted": None, "transitions": 3}

    monkeypatch.setattr(cli, "_run", _fake)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    assert cli.main([]) == 0


def test_main_abort_exits_three(monkeypatch, capsys):
    async def _fake(_args):
        return {"emitted": 0, "rejected": 0, "failed": 0, "aborted": "empty_archive"}

    monkeypatch.setattr(cli, "_run", _fake)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    assert cli.main([]) == cli.EXIT_ABORTED == 3


def test_main_auth_exits_two(monkeypatch, capsys):
    async def _fake(_args):
        raise DeliveryBlockedError("PM 403")

    monkeypatch.setattr(cli, "_run", _fake)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    assert cli.main([]) == 2


def test_main_failures_exit_one(monkeypatch, capsys):
    async def _fake(_args):
        return {"emitted": 1, "rejected": 1, "failed": 0, "aborted": None}

    monkeypatch.setattr(cli, "_run", _fake)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)
    assert cli.main([]) == 1
    assert json.loads(capsys.readouterr().out)["rejected"] == 1


def test_parser_defaults():
    args = cli._build_parser().parse_args([])
    assert args.dry_run is False
