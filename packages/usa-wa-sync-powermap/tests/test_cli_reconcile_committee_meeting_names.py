"""python -m usa_wa_sync_powermap.reconcile_committee_meeting_names CLI surface (#56).

Exercises arg parsing (incl. the storm-floor override), biennium resolution, the dry-run
``_run`` (session + meeting-cohort provider, no PM client), the submitting ``_run`` (PM
client built → reconcile → closed), the fail-closed api-key guard, and ``main``'s
arg-wiring + JSON + exit codes (with ``_run`` patched, since ``main`` spins its own event
loop via ``asyncio.run``).

The provider seam (:class:`MeetingCohortProvider`) is patched to return per-biennium
``{id: name}`` cohorts so the rename diff (current vs prior) runs end to end through the CLI.
"""

import json
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import DeliveryBlockedError, ObservationResult
from clearinghouse_sync_powermap.models import DISPOSITION_AUTO_ATTACHED
from usa_wa_sync_powermap import reconcile_committee_meeting_names as cli


def _patch_factory(monkeypatch, db_session):
    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(cli, "get_session_factory", lambda: _ctx)


def _patch_settings(monkeypatch, *, api_key="k"):
    monkeypatch.setattr(
        cli,
        "get_sidecar_settings",
        lambda: SimpleNamespace(powermap_api_key=api_key, powermap_base_url="http://pm"),
    )


def _patch_provider(monkeypatch, cohorts):
    """``cohorts`` maps biennium label → ``{id: name}`` (so current vs prior differ).

    Patches both the provider constructor (to a stub) and the WSLClient it would wrap, so no
    real WSDL is touched."""

    class _FakeProvider:
        def __init__(self, *_a, **_k):
            pass

        async def cohort(self, biennium):
            return dict(cohorts.get(biennium, {}))

    monkeypatch.setattr(cli, "MeetingCohortProvider", _FakeProvider)
    monkeypatch.setattr(cli, "WSLClient", lambda *_a, **_k: None)


async def _add_other(db_session, *, source_id, name, anchor):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=name,
        short_name="clean",
        org_type="other",
        pm_organization_id=anchor,
    )
    db_session.add(row)
    await db_session.flush()
    return row


# --- parser + biennium resolution --------------------------------------------


def test_parser_defaults():
    args = cli._build_parser().parse_args([])
    assert args.dry_run is False
    assert args.biennium is None
    assert args.max_rename_fraction == cli.DEFAULT_MAX_RENAME_FRACTION
    assert args.min_overlap_fraction == cli.DEFAULT_MIN_OVERLAP_FRACTION
    assert args.storm_floor_min_overlap == cli.DEFAULT_STORM_FLOOR_MIN_OVERLAP


def test_parser_accepts_overrides():
    args = cli._build_parser().parse_args(
        ["--biennium", "2023-24", "--min-overlap-fraction", "0.3", "--storm-floor-min-overlap", "8"]
    )
    assert args.biennium == "2023-24"
    assert args.min_overlap_fraction == 0.3
    assert args.storm_floor_min_overlap == 8


def test_resolve_biennium_arg_wins(monkeypatch):
    monkeypatch.setenv("USA_WA_BIENNIUM", "2099-00")
    assert cli._resolve_biennium("2025-26") == "2025-26"


def test_resolve_biennium_env_fallback(monkeypatch):
    monkeypatch.setenv("USA_WA_BIENNIUM", "2099-00")
    assert cli._resolve_biennium(None) == "2099-00"


def test_resolve_biennium_date_fallback(monkeypatch):
    monkeypatch.delenv("USA_WA_BIENNIUM", raising=False)
    assert re.fullmatch(r"\d{4}-\d{2}", cli._resolve_biennium(None))


# --- _run ---------------------------------------------------------------------


def _args(**over):
    base = dict(
        biennium="2025-26",
        dry_run=False,
        max_rename_fraction=1.0,
        min_overlap_fraction=0.0,
        storm_floor_min_overlap=5,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_run_dry_run_counts_without_pm_client(monkeypatch, db_session, usa_wa):
    await _add_other(db_session, source_id="-140", name="x", anchor=ULID())
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")  # absent key fine for a dry-run
    _patch_provider(
        monkeypatch,
        {"2025-26": {"-140": "New Name"}, "2023-24": {"-140": "Old Name"}},
    )

    result = await cli._run(_args(dry_run=True))

    assert result["dry_run"] is True
    assert result["renamed"] == 1
    assert result["emitted"] == 0


async def test_run_requires_api_key_when_submitting(monkeypatch, db_session):
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")
    _patch_provider(monkeypatch, {"2025-26": {"-140": "New Name"}})

    with pytest.raises(RuntimeError, match="POWERMAP_API_KEY"):
        await cli._run(_args())


async def test_run_submits_and_closes_client(monkeypatch, db_session, usa_wa):
    anchor = ULID()
    await _add_other(db_session, source_id="-140", name="x", anchor=anchor)
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="k")
    _patch_provider(
        monkeypatch,
        {"2025-26": {"-140": "New Name"}, "2023-24": {"-140": "Old Name"}},
    )

    closed = {"v": False}

    class _FakePM:
        def __init__(self, *_a, **_k):
            pass

        async def post_observation(self, _path, _payload):
            return ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=anchor, raw={})

        async def aclose(self):
            closed["v"] = True

    monkeypatch.setattr(cli, "GeneratedPowerMapClient", _FakePM)

    result = await cli._run(_args())

    assert result["emitted"] == 1
    assert closed["v"] is True


# --- main ---------------------------------------------------------------------


def test_main_wires_args_and_prints_json(monkeypatch, capsys):
    seen = {}

    async def _fake_run(args):
        seen["args"] = args
        return {"emitted": 1, "aborted": None, "rejected": 0, "failed": 0}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main(["--biennium", "2025-26"])

    assert rc == 0
    assert seen["args"].biennium == "2025-26"
    assert json.loads(capsys.readouterr().out)["emitted"] == 1


def test_main_abort_exits_distinct_code(monkeypatch, capsys):
    async def _fake_run(_args):
        return {"emitted": 0, "aborted": "empty_pull", "rejected": 0, "failed": 0}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main([])

    assert rc == cli.EXIT_ABORTED == 3
    assert json.loads(capsys.readouterr().out)["aborted"] == "empty_pull"


def test_main_nonzero_exit_on_failures(monkeypatch, capsys):
    async def _fake_run(_args):
        return {"emitted": 1, "aborted": None, "rejected": 1, "failed": 0}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    assert cli.main([]) == 1


def test_main_auth_block_exits_distinct_code(monkeypatch, capsys):
    async def _fake_run(_args):
        raise DeliveryBlockedError("PM 403 Insufficient scope")

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main([])

    assert rc == 2
    assert json.loads(capsys.readouterr().out)["error"].startswith("delivery blocked")
