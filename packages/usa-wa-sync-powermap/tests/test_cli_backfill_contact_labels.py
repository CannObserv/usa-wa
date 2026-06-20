"""python -m usa_wa_sync_powermap.backfill_contact_labels CLI surface (#31).

Exercises arg parsing, the dry-run ``_run`` (session open → count, no client) and
the submitting ``_run`` (client built → backfill → commit) against the savepointed
test session, the fail-closed api-key guard, and ``main``'s arg-wiring + JSON output
(with ``_run`` patched, since ``main`` spins its own event loop via ``asyncio.run``,
which cannot share the session-scoped test loop).
"""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import DeliveryBlockedError, ObservationResult
from clearinghouse_sync_powermap.models import DISPOSITION_AUTO_ATTACHED
from usa_wa_sync_powermap import backfill_contact_labels as cli


def _patch_factory(monkeypatch, db_session):
    """Make get_session_factory yield the savepointed test session."""

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


async def _add_phone_org(db_session, *, source_id, anchor):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=f"Org {source_id}",
        org_type="committee",
        phone="(360) 786-0000",
        pm_organization_id=anchor,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _add_acronym_only_org(db_session, *, source_id, anchor):
    """A committee that carries an acronym but no phone — outside the original
    phone-only cohort, still needing the object-shape acronym re-observe (#33)."""
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=f"Org {source_id}",
        org_type="committee",
        acronym="AGNR",
        phone=None,
        pm_organization_id=anchor,
    )
    db_session.add(row)
    await db_session.flush()
    return row


# --- parser -------------------------------------------------------------------


def test_parser_defaults():
    assert cli._build_parser().parse_args([]).dry_run is False


def test_parser_dry_run_flag():
    assert cli._build_parser().parse_args(["--dry-run"]).dry_run is True


# --- _run ---------------------------------------------------------------------


async def test_run_dry_run_counts_without_submitting(monkeypatch, db_session, usa_wa):
    """Dry-run opens only a session (no client), counts the cohort, mutates nothing."""
    await _add_phone_org(db_session, source_id="C-1", anchor=ULID())
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")  # absent key is fine for a read-only dry-run

    result = await cli._run(dry_run=True)

    assert result == {
        "scanned": 1,
        "accepted": 0,
        "rejected": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": True,
    }


async def test_dry_run_includes_acronym_only_org(monkeypatch, db_session, usa_wa):
    """Cohort covers an acronym-bearing org with no phone (#33): the #31 object-shape
    acronym fix only reaches already-anchored committees via a re-observe, and 4 WA
    committees carry an acronym but no phone — phone-only filtering would strand them."""
    await _add_acronym_only_org(db_session, source_id="A-1", anchor=ULID())
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")

    result = await cli._run(dry_run=True)

    assert result["scanned"] == 1


async def test_run_requires_api_key_when_submitting(monkeypatch, db_session):
    """Fail-closed: a real submission with no POWERMAP_API_KEY raises before any post."""
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")

    with pytest.raises(RuntimeError, match="POWERMAP_API_KEY"):
        await cli._run(dry_run=False)


async def test_run_submits_and_commits(monkeypatch, db_session, usa_wa):
    """The submitting path builds a client, re-observes the cohort, and closes it."""
    anchor = ULID()
    await _add_phone_org(db_session, source_id="C-2", anchor=anchor)
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="k")

    closed = {"v": False}

    class _FakeClient:
        def __init__(self, *_a, **_k):
            pass

        async def post_observation(self, _path, _payload):
            return ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=anchor, raw={})

        async def aclose(self):
            closed["v"] = True

    monkeypatch.setattr(cli, "GeneratedPowerMapClient", _FakeClient)

    result = await cli._run(dry_run=False)

    assert result["accepted"] == 1
    assert closed["v"] is True  # client always closed


def test_main_wires_args_and_prints_json(monkeypatch, capsys):
    """main parses --dry-run, calls _run with it, and prints the result as JSON."""
    seen = {}

    async def _fake_run(dry_run):
        seen["dry_run"] = dry_run
        return {"scanned": 2, "accepted": 0, "dry_run": dry_run}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main(["--dry-run"])

    assert rc == 0
    assert seen["dry_run"] is True
    assert json.loads(capsys.readouterr().out) == {"scanned": 2, "accepted": 0, "dry_run": True}


def test_main_nonzero_exit_on_failures(monkeypatch, capsys):
    """A run with any rejected/failed rows exits non-zero so $? signals it (#31 #10)."""

    async def _fake_run(dry_run):
        return {"scanned": 3, "accepted": 1, "rejected": 1, "failed": 1, "dry_run": dry_run}

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main([])

    assert rc == 1
    assert json.loads(capsys.readouterr().out)["failed"] == 1


def test_main_auth_block_exits_distinct_code(monkeypatch, capsys):
    """A global auth block surfaces as a one-line diagnostic + exit 2, not a traceback
    (#31 CR round-3 finding 13)."""

    async def _fake_run(dry_run):
        raise DeliveryBlockedError("PM 403 Insufficient scope")

    monkeypatch.setattr(cli, "_run", _fake_run)
    monkeypatch.setattr(cli, "configure_logging", lambda: None)

    rc = cli.main([])

    assert rc == 2
    body = json.loads(capsys.readouterr().out)
    assert body["error"].startswith("delivery blocked")


async def test_run_dry_run_leaves_rows_unmutated(monkeypatch, db_session, usa_wa):
    """A dry-run preview does not write an anchor or otherwise touch the rows."""
    await _add_phone_org(db_session, source_id="C-3", anchor=None)
    _patch_factory(monkeypatch, db_session)
    _patch_settings(monkeypatch, api_key="")

    await cli._run(dry_run=True)

    row = (await db_session.execute(select(Organization))).scalars().one()
    assert row.pm_organization_id is None
