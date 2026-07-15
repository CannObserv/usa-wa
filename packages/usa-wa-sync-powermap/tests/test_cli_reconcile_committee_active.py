"""python -m usa_wa_sync_powermap.reconcile_committee_active CLI surface (#44).

Exercises arg parsing, biennium resolution (arg → env → date), the dry-run ``_run``
(session + WSL client, no PM client), the submitting ``_run`` (PM client built →
reconcile → closed), the fail-closed api-key guard, and ``main``'s arg-wiring + JSON +
exit codes (with ``_run`` patched, since ``main`` spins its own event loop via
``asyncio.run``, which cannot share the session-scoped test loop).
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
from usa_wa_sync_powermap import reconcile_committee_active as cli


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


def _patch_wsl(monkeypatch, roster):
    class _FakeWSL:
        def __init__(self, *_a, **_k):
            pass

        async def get_committees(self, _biennium):
            return roster

    monkeypatch.setattr(cli, "WSLClient", _FakeWSL)


async def _add_committee(db_session, *, source_id, anchor, active=True):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=f"Org {source_id}",
        org_type="committee",
        active=active,
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
    assert args.max_absent_fraction == cli.DEFAULT_MAX_ABSENT_FRACTION


def test_parser_accepts_overrides():
    args = cli._build_parser().parse_args(["--biennium", "2023-24", "--max-absent-fraction", "0.9"])
    assert args.biennium == "2023-24"
    assert args.max_absent_fraction == 0.9


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


async def test_run_dry_run_counts_without_pm_client(monkeypatch, db_session, usa_wa):
    """Dry-run opens a session + WSL client (for the roster) but no PM client."""
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID())
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")  # absent key is fine for a dry-run
    _patch_wsl(monkeypatch, [{"Id": 100}])

    args = SimpleNamespace(biennium="2025-26", dry_run=True, max_absent_fraction=1.0)
    result = await cli._run(args)

    assert result["dry_run"] is True
    assert result["absent"] == 1
    assert result["retired"] == 0


async def test_run_requires_api_key_when_submitting(monkeypatch, db_session):
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")
    _patch_wsl(monkeypatch, [{"Id": 100}])

    args = SimpleNamespace(biennium="2025-26", dry_run=False, max_absent_fraction=1.0)
    with pytest.raises(RuntimeError, match="POWERMAP_API_KEY"):
        await cli._run(args)


async def test_run_submits_and_closes_client(monkeypatch, db_session, usa_wa):
    anchor = ULID()
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=anchor)
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="k")
    _patch_wsl(monkeypatch, [{"Id": 100}])

    closed = {"v": False}

    class _FakePM:
        def __init__(self, *_a, **_k):
            pass

        async def post_observation(self, _path, _payload):
            return ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=anchor, raw={})

        async def aclose(self):
            closed["v"] = True

    monkeypatch.setattr(cli, "build_pm_client", _FakePM)

    args = SimpleNamespace(biennium="2025-26", dry_run=False, max_absent_fraction=1.0)
    result = await cli._run(args)

    assert result["retired"] == 1
    assert closed["v"] is True  # client always closed


# --- main ---------------------------------------------------------------------


def test_main_wires_args_and_prints_json(monkeypatch, capsys):
    seen = {}

    async def _fake_run(args):
        seen["args"] = args
        return {"retired": 1, "aborted": None, "rejected": 0, "failed": 0}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main(["--biennium", "2025-26"])

    assert rc == 0
    assert seen["args"].biennium == "2025-26"
    assert json.loads(capsys.readouterr().out)["retired"] == 1


def test_main_abort_exits_distinct_code(monkeypatch, capsys):
    """A guardrail abort exits with EXIT_ABORTED (3) — distinct from a partial-failure 1
    so a cron can tell "took no action" from "acted, some rows failed"."""

    async def _fake_run(_args):
        return {"retired": 0, "aborted": "cohort_floor", "rejected": 0, "failed": 0}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main([])

    assert rc == cli.EXIT_ABORTED == 3
    assert json.loads(capsys.readouterr().out)["aborted"] == "cohort_floor"


def test_main_nonzero_exit_on_failures(monkeypatch, capsys):
    async def _fake_run(_args):
        return {"retired": 1, "aborted": None, "rejected": 1, "failed": 0}

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
