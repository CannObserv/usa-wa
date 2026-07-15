"""python -m usa_wa_sync_powermap.prune_subscriptions CLI surface (#73 Axis 1 step 6).

Exercises arg parsing, the fail-closed api-key guard, ``_run``'s client build →
prune → close, and ``main``'s arg wiring + JSON + exit codes (0 clean, 2 auth block,
3 guardrail abort). ``main`` tests patch ``_run`` since it spins its own event loop via
``asyncio.run``, which cannot share the session-scoped test loop. The prune diff logic
itself is covered in the portable ``test_subscriptions`` suite.
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import DeliveryBlockedError
from clearinghouse_sync_powermap.subscriptions import DEFAULT_MAX_PRUNE_FRACTION
from usa_wa_sync_powermap import prune_subscriptions as cli
from usa_wa_sync_powermap.config import SidecarSettings


def _patch_factory(monkeypatch, db_session):
    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(cli, "get_session_factory", lambda: _ctx)


def _patch_settings(monkeypatch, *, api_key="k"):
    # A real SidecarSettings — _run threads it through build_descriptors + build_reconciler,
    # which read the discovery/cadence/match-cap fields a bare SimpleNamespace lacks.
    monkeypatch.setattr(
        cli,
        "get_sidecar_settings",
        lambda: SidecarSettings(powermap_api_key=api_key, powermap_base_url="http://pm"),
    )


class _FakePM:
    """PM double exposing just the subscription surface the prune touches."""

    def __init__(self, *, registered, discovered=None):
        self.subscribed = list(registered)
        self._discovered = list(discovered or [])
        self.removed: list[list] = []
        self.closed = False

    async def discover(self, *, root_type, root_id, follow, limit=100, offset=0):
        return list(self._discovered)

    async def list_subscriptions(self, *, entity_type=None):
        return list(self.subscribed)

    async def remove_subscriptions(self, entity_ids):
        ids = list(entity_ids)
        self.removed.append(ids)
        before = len(self.subscribed)
        self.subscribed = [i for i in self.subscribed if i not in ids]
        return before - len(self.subscribed)

    async def aclose(self):
        self.closed = True


async def _add_committee(db_session, *, source_id, anchor):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=f"Org {source_id}",
        org_type="committee",
        active=True,
        pm_organization_id=anchor,
    )
    db_session.add(row)
    await db_session.flush()
    return row


# --- parser -------------------------------------------------------------------


def test_parser_defaults():
    args = cli._build_parser().parse_args([])
    assert args.dry_run is False
    assert args.max_prune_fraction == DEFAULT_MAX_PRUNE_FRACTION


def test_parser_accepts_overrides():
    args = cli._build_parser().parse_args(["--dry-run", "--max-prune-fraction", "0.5"])
    assert args.dry_run is True
    assert args.max_prune_fraction == 0.5


# --- _run ---------------------------------------------------------------------


async def test_run_requires_api_key(monkeypatch, db_session):
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")

    args = SimpleNamespace(dry_run=False, max_prune_fraction=DEFAULT_MAX_PRUNE_FRACTION)
    try:
        await cli._run(args)
    except RuntimeError as exc:
        assert "POWERMAP_API_KEY" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected a fail-closed RuntimeError")


async def test_run_prunes_strangers_and_closes_client(monkeypatch, db_session, usa_wa):
    """A produced committee keeps its subscription (it is in the local cohort); a
    stranger registration not in the mirror set is unsubscribed. Client always closed."""
    anchor, stranger = ULID(), ULID()
    await _add_committee(db_session, source_id="100", anchor=anchor)
    fake_pm = _FakePM(registered=[anchor, stranger], discovered=[])
    monkeypatch.setattr(cli, "build_pm_client", lambda *_a, **_k: fake_pm)
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="k")

    args = SimpleNamespace(dry_run=False, max_prune_fraction=DEFAULT_MAX_PRUNE_FRACTION)
    result = await cli._run(args)

    assert result["removed"] == 1
    assert fake_pm.removed == [[stranger]]
    assert fake_pm.subscribed == [anchor]  # produced row kept
    assert fake_pm.closed is True


async def test_run_dry_run_removes_nothing(monkeypatch, db_session, usa_wa):
    anchor, stranger = ULID(), ULID()
    await _add_committee(db_session, source_id="100", anchor=anchor)
    fake_pm = _FakePM(registered=[anchor, stranger], discovered=[])
    monkeypatch.setattr(cli, "build_pm_client", lambda *_a, **_k: fake_pm)
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="k")

    args = SimpleNamespace(dry_run=True, max_prune_fraction=DEFAULT_MAX_PRUNE_FRACTION)
    result = await cli._run(args)

    assert result["stale"] == 1
    assert result["removed"] == 0
    assert fake_pm.removed == []


# --- main ---------------------------------------------------------------------


def test_main_prints_json_and_exits_zero(monkeypatch, capsys):
    async def _fake_run(_args):
        return {"registered": 2, "stale": 1, "removed": 1, "aborted": None, "dry_run": False}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main([])

    assert rc == 0
    assert json.loads(capsys.readouterr().out)["removed"] == 1


def test_main_abort_exits_three(monkeypatch, capsys):
    async def _fake_run(_args):
        return {"registered": 2, "stale": 2, "removed": 0, "aborted": "prune_floor"}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main([])

    assert rc == cli.EXIT_ABORTED
    assert json.loads(capsys.readouterr().out)["aborted"] == "prune_floor"


def test_main_auth_block_exits_two(monkeypatch, capsys):
    async def _fake_run(_args):
        raise DeliveryBlockedError("PM 403")

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main([])

    assert rc == 2
    assert "delivery blocked" in json.loads(capsys.readouterr().out)["error"]
